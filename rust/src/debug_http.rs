//! Optional, loopback-only HTTP view of native server state.

use std::{
    io::{self, Read, Write},
    net::{Ipv4Addr, SocketAddr, TcpListener},
    sync::{Arc, mpsc},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use serde::Deserialize;

use crate::{
    debug_geometry::{DebugGeometry, GeometryRegion},
    game_debug::{MAX_DEBUG_STEP_BUDGET, RoomControlCommand, RoomControlStatus, Snapshot},
    service_tcp::{PeerStream, PeerWorkers, accept},
};

const ACCEPT_POLL: Duration = Duration::from_millis(10);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_PEERS: usize = 8;
const MAX_REQUEST_BYTES: usize = 8192;
const MAX_REGION_QUERY_BYTES: usize = 128;
const MAX_CONTROL_BODY_BYTES: usize = 256;
const HTML: &str = include_str!("../assets/inspector/index.html");
const CSS: &str = include_str!("../assets/inspector/debug.css");
const SCRIPT: &str = include_str!("../assets/inspector/debug.js");

type ControlHandler =
    Box<dyn Fn([u64; 2], RoomControlCommand) -> io::Result<RoomControlStatus> + Send + Sync>;

pub(crate) struct DebugHttp {
    addr: SocketAddr,
    stop: mpsc::Sender<()>,
    worker: Option<JoinHandle<io::Result<()>>>,
}

impl DebugHttp {
    pub(crate) fn bind(
        port: u16,
        geometry: DebugGeometry,
        state: impl Fn() -> io::Result<Snapshot> + Send + Sync + 'static,
    ) -> io::Result<Self> {
        Self::bind_inner(port, geometry, Box::new(state), None)
    }

    pub(crate) fn bind_with_control(
        port: u16,
        geometry: DebugGeometry,
        state: impl Fn() -> io::Result<Snapshot> + Send + Sync + 'static,
        control: impl Fn([u64; 2], RoomControlCommand) -> io::Result<RoomControlStatus>
        + Send
        + Sync
        + 'static,
    ) -> io::Result<Self> {
        Self::bind_inner(port, geometry, Box::new(state), Some(Box::new(control)))
    }

    fn bind_inner(
        port: u16,
        geometry: DebugGeometry,
        state: Box<dyn Fn() -> io::Result<Snapshot> + Send + Sync>,
        control: Option<ControlHandler>,
    ) -> io::Result<Self> {
        let initial_geometry = serde_json::to_vec(&geometry.view()).map_err(io::Error::other)?;
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, port))?;
        listener.set_nonblocking(true)?;
        let addr = listener.local_addr()?;
        let host = addr.to_string();
        let endpoint = Arc::new(Endpoint {
            origin: format!("http://{host}"),
            host,
            geometry,
            initial_geometry,
            state,
            control,
        });
        let (stop, shutdown) = mpsc::channel();
        let worker = thread::Builder::new()
            .name("debug-http".into())
            .spawn(move || serve(listener, endpoint, shutdown))?;
        Ok(Self {
            addr,
            stop,
            worker: Some(worker),
        })
    }

    pub(crate) fn addr(&self) -> SocketAddr {
        self.addr
    }

    pub(crate) fn stop(&mut self) -> io::Result<()> {
        if let Some(worker) = self.worker.take() {
            let _ = self.stop.send(());
            worker
                .join()
                .map_err(|_| io::Error::other("debug HTTP listener thread panicked"))??;
        }
        Ok(())
    }
}

impl Drop for DebugHttp {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

struct Endpoint {
    host: String,
    origin: String,
    geometry: DebugGeometry,
    initial_geometry: Vec<u8>,
    state: Box<dyn Fn() -> io::Result<Snapshot> + Send + Sync>,
    control: Option<ControlHandler>,
}

struct ParsedHeaders {
    origin_seen: bool,
    content_length: Option<usize>,
    content_type_seen: bool,
    content_type_json: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ControlRequest {
    action: String,
    steps: Option<u32>,
}

enum RoomControlRoute {
    Valid([u64; 2]),
    InvalidId,
    Other,
}

fn serve(
    listener: TcpListener,
    endpoint: Arc<Endpoint>,
    shutdown: mpsc::Receiver<()>,
) -> io::Result<()> {
    let mut peers = PeerWorkers::default();
    while matches!(shutdown.try_recv(), Err(mpsc::TryRecvError::Empty)) {
        peers.reap();
        if peers.len() < MAX_PEERS {
            if let Some((socket, _)) = accept(&listener)? {
                let endpoint = Arc::clone(&endpoint);
                let _ = peers.spawn("debug-http-peer", socket, move |socket| {
                    let _ = serve_peer(socket, &endpoint);
                });
                continue;
            }
        }
        if !matches!(
            shutdown.recv_timeout(ACCEPT_POLL),
            Err(mpsc::RecvTimeoutError::Timeout)
        ) {
            break;
        }
    }
    Ok(())
}

fn serve_peer(mut socket: PeerStream, endpoint: &Endpoint) -> io::Result<()> {
    socket.set_deadline(Some(Instant::now() + REQUEST_TIMEOUT));
    let mut buffer = [0; MAX_REQUEST_BYTES];
    let mut filled = 0_usize;
    loop {
        let read = socket.read(&mut buffer[filled..])?;
        if read == 0 {
            return Ok(());
        }
        let scan_start = filled.saturating_sub(3);
        filled += read;
        if buffer[scan_start..filled]
            .windows(4)
            .any(|bytes| bytes == b"\r\n\r\n")
        {
            break;
        }
        if filled == buffer.len() {
            return error_response(&mut socket, "431 Request Header Fields Too Large");
        }
    }

    let mut raw_headers = [httparse::EMPTY_HEADER; 32];
    let mut request = httparse::Request::new(&mut raw_headers);
    let body_start = match request.parse(&buffer[..filled]) {
        Ok(httparse::Status::Complete(end)) => end,
        Err(httparse::Error::TooManyHeaders) => {
            return error_response(&mut socket, "431 Request Header Fields Too Large");
        }
        _ => return error_response(&mut socket, "400 Bad Request"),
    };

    let Some(headers) = parse_headers(request.headers, endpoint) else {
        return error_response(&mut socket, "400 Bad Request");
    };
    let path = request.path.unwrap_or_default();
    let (route, query) = path
        .split_once('?')
        .map_or((path, None), |(route, query)| (route, Some(query)));

    match request.method {
        Some("GET") => {
            if headers.content_length.unwrap_or(0) != 0 || body_start != filled {
                return error_response(&mut socket, "400 Bad Request");
            }
            if is_room_control_path(route) && query.is_none() {
                if endpoint.control.is_some() {
                    return error_response_allow(
                        &mut socket,
                        "405 Method Not Allowed",
                        Some("POST"),
                    );
                }
                return error_response(&mut socket, "404 Not Found");
            }
            match (route, query) {
                ("/", None) => respond(
                    &mut socket,
                    "200 OK",
                    "text/html; charset=utf-8",
                    HTML.as_bytes(),
                ),
                ("/debug.css", None) => respond(
                    &mut socket,
                    "200 OK",
                    "text/css; charset=utf-8",
                    CSS.as_bytes(),
                ),
                ("/debug.js", None) => respond(
                    &mut socket,
                    "200 OK",
                    "text/javascript; charset=utf-8",
                    SCRIPT.as_bytes(),
                ),
                ("/api/debug/v1/geometry", None) => respond(
                    &mut socket,
                    "200 OK",
                    "application/json",
                    &endpoint.initial_geometry,
                ),
                ("/api/debug/v1/geometry", Some(query)) => {
                    let Some(region) = parse_geometry_region(query) else {
                        return error_response(&mut socket, "400 Bad Request");
                    };
                    let body = serde_json::to_vec(&endpoint.geometry.region(region))
                        .map_err(io::Error::other)?;
                    respond(&mut socket, "200 OK", "application/json", &body)
                }
                ("/api/debug/v1/state", None) => {
                    let Ok(state) = (endpoint.state)() else {
                        return error_response(&mut socket, "503 Service Unavailable");
                    };
                    let body = serde_json::to_vec(&state).map_err(io::Error::other)?;
                    respond(&mut socket, "200 OK", "application/json", &body)
                }
                _ => error_response(&mut socket, "404 Not Found"),
            }
        }
        Some("POST") => {
            let Some(control) = &endpoint.control else {
                return error_response_allow(&mut socket, "405 Method Not Allowed", Some("GET"));
            };
            if is_read_only_get_route(route) {
                return error_response_allow(&mut socket, "405 Method Not Allowed", Some("GET"));
            }
            let room_id = match parse_room_control_route(route, query) {
                RoomControlRoute::Valid(id) => id,
                RoomControlRoute::InvalidId => {
                    return error_response(&mut socket, "400 Bad Request");
                }
                RoomControlRoute::Other => return error_response(&mut socket, "404 Not Found"),
            };
            if !headers.origin_seen {
                return error_response(&mut socket, "400 Bad Request");
            }
            if !headers.content_type_seen || !headers.content_type_json {
                return error_response(&mut socket, "415 Unsupported Media Type");
            }
            let Some(content_length) = headers.content_length else {
                return error_response(&mut socket, "400 Bad Request");
            };
            if content_length == 0 {
                return error_response(&mut socket, "400 Bad Request");
            }
            if content_length > MAX_CONTROL_BODY_BYTES {
                return error_response(&mut socket, "413 Payload Too Large");
            }
            let initial_body = &buffer[body_start..filled];
            if initial_body.len() > content_length {
                return error_response(&mut socket, "400 Bad Request");
            }
            let mut body = Vec::with_capacity(content_length);
            body.extend_from_slice(initial_body);
            let mut chunk = [0_u8; MAX_CONTROL_BODY_BYTES + 1];
            while body.len() < content_length {
                let remaining = content_length - body.len();
                let read = socket.read(&mut chunk[..remaining + 1])?;
                if read == 0 || read > remaining {
                    return error_response(&mut socket, "400 Bad Request");
                }
                body.extend_from_slice(&chunk[..read]);
            }
            let Some(command) = parse_control_command(&body) else {
                return error_response(&mut socket, "400 Bad Request");
            };
            match control(room_id, command) {
                Ok(status) => {
                    let body = serde_json::to_vec(&status).map_err(io::Error::other)?;
                    respond(&mut socket, "200 OK", "application/json", &body)
                }
                Err(error) => {
                    let status = match error.kind() {
                        io::ErrorKind::NotFound => "404 Not Found",
                        io::ErrorKind::InvalidInput => "400 Bad Request",
                        io::ErrorKind::PermissionDenied => "403 Forbidden",
                        io::ErrorKind::WouldBlock => "429 Too Many Requests",
                        _ => "503 Service Unavailable",
                    };
                    error_response(&mut socket, status)
                }
            }
        }
        _ => {
            if endpoint.control.is_some() && is_room_control_path(route) && query.is_none() {
                error_response_allow(&mut socket, "405 Method Not Allowed", Some("POST"))
            } else {
                error_response_allow(&mut socket, "405 Method Not Allowed", Some("GET"))
            }
        }
    }
}

fn is_read_only_get_route(route: &str) -> bool {
    matches!(
        route,
        "/" | "/debug.css" | "/debug.js" | "/api/debug/v1/geometry" | "/api/debug/v1/state"
    )
}

fn is_room_control_path(route: &str) -> bool {
    route
        .strip_prefix("/api/debug/v1/rooms/")
        .and_then(|rest| rest.strip_suffix("/control"))
        .is_some_and(|id| !id.is_empty() && !id.contains('/'))
}

fn parse_room_control_route(route: &str, query: Option<&str>) -> RoomControlRoute {
    let Some(rest) = route.strip_prefix("/api/debug/v1/rooms/") else {
        return RoomControlRoute::Other;
    };
    let Some(raw) = rest.strip_suffix("/control") else {
        return RoomControlRoute::Other;
    };
    if raw.is_empty() || raw.contains('/') {
        return RoomControlRoute::Other;
    }
    if query.is_some() || raw.len() != 32 || !raw.bytes().all(|b| b.is_ascii_hexdigit()) {
        return RoomControlRoute::InvalidId;
    }
    let Ok(hi) = u64::from_str_radix(&raw[..16], 16) else {
        return RoomControlRoute::InvalidId;
    };
    let Ok(lo) = u64::from_str_radix(&raw[16..], 16) else {
        return RoomControlRoute::InvalidId;
    };
    RoomControlRoute::Valid([hi, lo])
}

fn parse_control_command(body: &[u8]) -> Option<RoomControlCommand> {
    let parsed: ControlRequest = serde_json::from_slice(body).ok()?;
    match (parsed.action.as_str(), parsed.steps) {
        ("pause", None) => Some(RoomControlCommand::Pause),
        ("resume", None) => Some(RoomControlCommand::Resume),
        ("step", None) => Some(RoomControlCommand::Step(1)),
        ("step", Some(steps)) if (1..=MAX_DEBUG_STEP_BUDGET).contains(&steps) => {
            Some(RoomControlCommand::Step(steps))
        }
        _ => None,
    }
}

fn parse_geometry_region(query: &str) -> Option<GeometryRegion> {
    if query.len() > MAX_REGION_QUERY_BYTES {
        return None;
    }
    let (mut center, mut half_extent) = (None, None);
    for parameter in query.split('&') {
        match parameter.split_once('=')? {
            ("center", value) if center.is_none() => center = Some(value),
            ("half_extent", value) if half_extent.is_none() => {
                half_extent = Some(value.parse().ok()?)
            }
            _ => return None,
        }
    }
    let mut axes = center?.split(',');
    let center = [
        axes.next()?.parse().ok()?,
        axes.next()?.parse().ok()?,
        axes.next()?.parse().ok()?,
    ];
    if axes.next().is_some() {
        return None;
    }
    GeometryRegion::new(center, half_extent?)
}

fn parse_headers(headers: &[httparse::Header<'_>], endpoint: &Endpoint) -> Option<ParsedHeaders> {
    let mut host_seen = false;
    let mut origin_seen = false;
    let mut content_length = None;
    let mut content_type_seen = false;
    let mut content_type_json = false;
    for header in headers {
        if header.name.eq_ignore_ascii_case("host") {
            if host_seen || header.value != endpoint.host.as_bytes() {
                return None;
            }
            host_seen = true;
        } else if header.name.eq_ignore_ascii_case("origin") {
            if origin_seen || header.value != endpoint.origin.as_bytes() {
                return None;
            }
            origin_seen = true;
        } else if header.name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                return None;
            }
            let raw = std::str::from_utf8(header.value).ok()?;
            if raw.is_empty() || !raw.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            content_length = Some(raw.parse::<usize>().ok()?);
        } else if header.name.eq_ignore_ascii_case("content-type") {
            if content_type_seen {
                return None;
            }
            content_type_seen = true;
            if let Ok(raw) = std::str::from_utf8(header.value) {
                let normalized = raw.trim().to_ascii_lowercase();
                content_type_json = matches!(
                    normalized.as_str(),
                    "application/json"
                        | "application/json; charset=utf-8"
                        | "application/json;charset=utf-8"
                );
            }
        } else if header.name.eq_ignore_ascii_case("transfer-encoding") {
            return None;
        }
    }
    if !host_seen {
        return None;
    }
    Some(ParsedHeaders {
        origin_seen,
        content_length,
        content_type_seen,
        content_type_json,
    })
}

fn error_response(socket: &mut PeerStream, status: &str) -> io::Result<()> {
    let allow = if status == "405 Method Not Allowed" {
        Some("GET")
    } else {
        None
    };
    error_response_allow(socket, status, allow)
}

fn error_response_allow(
    socket: &mut PeerStream,
    status: &str,
    allow: Option<&str>,
) -> io::Result<()> {
    respond_with_allow(
        socket,
        status,
        "text/plain; charset=utf-8",
        status.as_bytes(),
        allow,
    )
}

fn respond(
    socket: &mut PeerStream,
    status: &str,
    content_type: &str,
    body: &[u8],
) -> io::Result<()> {
    respond_with_allow(socket, status, content_type, body, None)
}

fn respond_with_allow(
    socket: &mut PeerStream,
    status: &str,
    content_type: &str,
    body: &[u8],
    allow: Option<&str>,
) -> io::Result<()> {
    let allow = match allow {
        Some("POST") => "Allow: POST\r\n",
        Some("GET") => "Allow: GET\r\n",
        _ => "",
    };
    write!(
        socket,
        "HTTP/1.1 {status}\r\n\
         Content-Type: {content_type}\r\n\
         Content-Length: {}\r\n\
         Cache-Control: no-store\r\n\
         Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'\r\n\
         X-Content-Type-Options: nosniff\r\n\
         Cross-Origin-Resource-Policy: same-origin\r\n\
         Referrer-Policy: no-referrer\r\n\
         {allow}\
         Connection: close\r\n\r\n",
        body.len(),
    )?;
    socket.write_all(body)
}

#[cfg(test)]
mod tests {
    use std::{collections::BTreeSet, net::TcpStream};

    use super::*;
    use crate::{
        collision_cache::CollisionCache, collision_scene::CollisionScene, game_content::GameMap,
    };
    use serde_json::{Value, json};

    fn fixture_geometry() -> DebugGeometry {
        let cache = CollisionCache {
            header: serde_json::from_value(json!({
                "meshes": [], "hulls": 0, "models": 0, "placements": 0, "identifiers": [],
            }))
            .unwrap(),
            meshes: vec![],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        DebugGeometry::new(
            &GameMap::practice_range(),
            "fixture.col".into(),
            0x18,
            Arc::new(CollisionScene::movement_probe(&cache, &BTreeSet::new()).unwrap()),
            None,
        )
    }

    fn request(addr: SocketAddr, raw: &str) -> String {
        let mut socket = TcpStream::connect(addr).unwrap();
        socket.set_read_timeout(Some(REQUEST_TIMEOUT)).unwrap();
        socket.write_all(raw.as_bytes()).unwrap();
        let mut response = String::new();
        socket.read_to_string(&mut response).unwrap();
        response
    }

    #[test]
    fn serves_loopback_assets_geometry_and_live_state() {
        let mut http = DebugHttp::bind(0, fixture_geometry(), || {
            Ok(Snapshot {
                tick: 42,
                rooms: Vec::new(),
                truncated: false,
                control_enabled: false,
                timing: None,
                ai_policy: None,
                faults: vec![crate::game_debug::RoomFault {
                    room: "00000000000000050000000000000008".into(),
                    stage: "finish_tick",
                    connection: None,
                    tick: 41,
                    reason: "panic",
                }],
            })
        })
        .unwrap();
        let addr = http.addr();
        assert_eq!(addr.ip(), Ipv4Addr::LOCALHOST);

        let response = request(addr, &format!("GET / HTTP/1.1\r\nHost: {addr}\r\n\r\n"));
        assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
        assert!(response.contains("Content-Security-Policy:"));
        assert!(response.contains("Cache-Control: no-store"));
        assert!(response.contains("ORPHEUS"));

        for path in ["/debug.css", "/debug.js"] {
            let response = request(
                addr,
                &format!("GET {path} HTTP/1.1\r\nHost: {addr}\r\n\r\n"),
            );
            assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
        }
        for (route, expected) in [
            (
                "geometry",
                json!({"map": "0800000000000688", "origin_source": "first-player-spawn"}),
            ),
            (
                "state",
                json!({"tick": 42, "rooms": [], "truncated": false,
                    "faults": [{"room": "00000000000000050000000000000008",
                        "stage": "finish_tick", "connection": null,
                        "tick": 41, "reason": "panic"}]}),
            ),
        ] {
            let response = request(
                addr,
                &format!(
                    "GET /api/debug/v1/{route} HTTP/1.1\r\nHost: {addr}\r\nOrigin: http://{addr}\r\n\r\n"
                ),
            );
            assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
            assert!(response.contains("Content-Type: application/json"));
            let (_, body) = response.split_once("\r\n\r\n").unwrap();
            let body: Value = serde_json::from_str(body).unwrap();
            for (field, expected) in expected.as_object().unwrap() {
                assert_eq!(&body[field], expected);
            }
        }

        http.stop().unwrap();
        assert!(TcpStream::connect(addr).is_err());
        http.stop().unwrap();
    }

    #[test]
    fn serves_requested_geometry_and_rejects_invalid_regions() {
        let http = DebugHttp::bind(0, fixture_geometry(), || unreachable!()).unwrap();
        let addr = http.addr();
        let query = |parameters| {
            request(
                addr,
                &format!(
                    "GET /api/debug/v1/geometry?{parameters} HTTP/1.1\r\nHost: {addr}\r\n\r\n"
                ),
            )
        };
        let response = query("half_extent=2&center=200,18,-4");
        assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
        let (_, body) = response.split_once("\r\n\r\n").unwrap();
        let sample = serde_json::from_str::<Value>(body).unwrap();
        assert_eq!(sample["origin"], json!([200.0, 18.0, -4.0]));
        assert_eq!(sample["origin_source"], "requested-center");
        assert_eq!(sample["bounds"]["min"], json!([198.0, 16.0, -6.0]));
        assert_eq!(sample["bounds"]["max"], json!([202.0, 20.0, -2.0]));
        assert_eq!(sample["sources"]["movement_cache"], "fixture.col");
        assert_eq!(
            sample["truncated"],
            json!({"triangles": false, "proxies": false})
        );

        for parameters in [
            "",
            "center=0,0,0",
            "center=0,0&half_extent=2",
            "center=0,0,0,0&half_extent=2",
            "center=0,0,0&half_extent=NaN",
            "center=0,inf,0&half_extent=2",
            "center=0,0,-65537&half_extent=2",
            "center=0,0,0&half_extent=0.5",
            "center=0,0,0&half_extent=17",
            "center=0,0,0&half_extent=2&center=1,1,1",
            "center=0,0,0&half_extent=2&half_extent=3",
            "center=0,0,0&half_extent=oops&half_extent=3",
            "center=0,0,0&half_extent=2&extra=1",
        ] {
            let response = query(parameters);
            assert!(
                response.starts_with("HTTP/1.1 400 "),
                "{parameters}: {response}"
            );
        }
        let overlong = format!(
            "center=0,0,0&half_extent=2.{}",
            "0".repeat(MAX_REGION_QUERY_BYTES)
        );
        assert!(query(&overlong).starts_with("HTTP/1.1 400 "));
    }

    #[test]
    fn caps_request_bytes_and_header_count() {
        let http = DebugHttp::bind(0, fixture_geometry(), || unreachable!()).unwrap();
        let addr = http.addr();
        let mut request_line = format!("GET / HTTP/1.1\r\nHost: {addr}\r\nLong: ");
        request_line.push_str(&"x".repeat(MAX_REQUEST_BYTES - request_line.len()));
        let too_many = format!(
            "GET / HTTP/1.1\r\nHost: {addr}\r\n{}\r\n",
            "X: x\r\n".repeat(32)
        );
        for raw in [request_line, too_many] {
            let response = request(addr, &raw);
            assert!(response.starts_with("HTTP/1.1 431"), "{response}");
        }
    }

    #[test]
    fn refuses_untrusted_requests_and_hides_snapshot_errors() {
        let http = DebugHttp::bind(0, fixture_geometry(), || {
            Err(io::Error::other("private simulation error"))
        })
        .unwrap();
        let addr = http.addr();
        let cases = [
            (format!("POST / HTTP/1.1\r\nHost: {addr}\r\n\r\n"), "405"),
            ("GET / HTTP/1.1\r\nHost: example.test\r\n\r\n".into(), "400"),
            (
                format!("GET / HTTP/1.1\r\nHost: {addr}\r\nHost: {addr}\r\n\r\n"),
                "400",
            ),
            (
                format!("GET / HTTP/1.1\r\nHost: {addr}\r\nOrigin: https://example.test\r\n\r\n"),
                "400",
            ),
            (
                format!("GET / HTTP/1.1\r\nHost: {addr}\r\nContent-Length: 1\r\n\r\n"),
                "400",
            ),
            (
                format!("GET / HTTP/1.1\r\nHost: {addr}\r\nTransfer-Encoding: chunked\r\n\r\n"),
                "400",
            ),
            (
                format!("GET /unknown HTTP/1.1\r\nHost: {addr}\r\n\r\n"),
                "404",
            ),
            (
                format!("GET /api/debug/v1/state HTTP/1.1\r\nHost: {addr}\r\n\r\n"),
                "503",
            ),
        ];
        for (raw, status) in cases {
            let response = request(addr, &raw);
            assert!(
                response.starts_with(&format!("HTTP/1.1 {status}")),
                "{response}"
            );
            assert!(!response.contains("private simulation error"));
        }
    }

    #[test]
    fn serves_physics_navigation_and_script_overlays_read_only() {
        use crate::game_debug::{
            Actor, ActorKind, DebugBotRoute, DebugContactNormal, DebugGroundProbe, DebugRay,
            DebugSpawnCandidate, EntityScriptSnapshot, NavigationOverlay, NavigationTruncation,
            PhysicsOverlay, PhysicsTruncation, Room, ScriptInstanceSummary, ScriptStateSummary,
            ScriptVarSummary,
        };

        let http = DebugHttp::bind(0, fixture_geometry(), || {
            Ok(Snapshot {
                tick: 99,
                rooms: vec![Room {
                    id: "00000000000000050000000000000008".into(),
                    map: "0800000000000688".into(),
                    name: "Practice Range".into(),
                    actors: vec![Actor {
                        entity: 0xc000_1000,
                        generation: None,
                        kind: ActorKind::Bot,
                        team: 1,
                        position: [1.0, 0.0, 2.0],
                        velocity: [0.0; 3],
                        alive: true,
                        health: Some(200.0),
                        script_active: true,
                        last_contact_clip: None,
                    }],
                    encounter: None,
                    ai_generation: Some(1),
                    control: None,
                    physics: Some(PhysicsOverlay {
                        rays: vec![DebugRay {
                            tick: 99,
                            source: 0xa000_0001,
                            kind: "hitscan",
                            origin: [0.0, 1.5, 0.0],
                            end: [1.0, 1.5, 2.0],
                            hit: "actor",
                            target: Some(0xc000_1000),
                            critical: true,
                        }],
                        contact_normals: vec![DebugContactNormal {
                            tick: 99,
                            entity: 0xa000_0001,
                            point: [0.0, 0.0, 1.0],
                            normal: [0.0, 0.0, -1.0],
                        }],
                        ground_probes: vec![DebugGroundProbe {
                            tick: 99,
                            entity: 0xa000_0001,
                            origin: [0.0, 0.1, 0.0],
                            hit: Some([0.0, 0.0, 0.0]),
                            normal: Some([0.0, 1.0, 0.0]),
                            grounded: true,
                            non_ground_under: false,
                        }],
                        truncated: PhysicsTruncation::default(),
                    }),
                    navigation: Some(NavigationOverlay {
                        routes: vec![DebugBotRoute {
                            entity: 0xc000_1000,
                            kind: "patrol",
                            patrol_index: Some(0),
                            waypoints: vec![[1.0, 0.0, 2.0], [4.0, 0.0, 2.0]],
                            target: Some([4.0, 0.0, 2.0]),
                            target_entity: None,
                            path: Vec::new(),
                            truncated: false,
                        }],
                        spawn_candidates: vec![DebugSpawnCandidate {
                            tick: 99,
                            instance: 2,
                            state: 11,
                            origin: [70.0, 12.0, -100.0],
                            candidate: [72.0, 12.0, -98.0],
                            accepted: true,
                            landed: Some([72.0, 12.0, -98.0]),
                        }],
                        truncated: NavigationTruncation::default(),
                    }),
                    scripts: vec![EntityScriptSnapshot {
                        entity: 0xa000_0001,
                        kind: "player",
                        now_ms: 1584,
                        diagnostics: 0,
                        instances: vec![ScriptInstanceSummary {
                            id: 1,
                            graph: "0x0043".into(),
                            parent: None,
                            active_states: vec![ScriptStateSummary {
                                raw: 6,
                                kind: "Ability",
                                activation: 1,
                                start_ms: 1500,
                                flags: Some(7),
                            }],
                            states_truncated: false,
                            variables: vec![ScriptVarSummary {
                                id: 61,
                                value: "1.000".into(),
                                resolved: true,
                            }],
                            variables_truncated: false,
                        }],
                        instances_truncated: false,
                        variables: Vec::new(),
                        variables_truncated: false,
                    }],
                    scripts_truncated: true,
                }],
                truncated: false,
                control_enabled: false,
                timing: None,
                ai_policy: None,
                faults: Vec::new(),
            })
        })
        .unwrap();
        let addr = http.addr();

        let index = request(addr, &format!("GET / HTTP/1.1\r\nHost: {addr}\r\n\r\n"));
        assert!(index.starts_with("HTTP/1.1 200 OK\r\n"));
        assert!(index.contains("id=\"physics\""));
        assert!(index.contains("id=\"navigation\""));
        assert!(index.contains("id=\"vm-panel\""));

        let js = request(
            addr,
            &format!("GET /debug.js HTTP/1.1\r\nHost: {addr}\r\n\r\n"),
        );
        assert!(js.starts_with("HTTP/1.1 200 OK\r\n"));
        assert!(js.contains("renderScripts"));
        assert!(js.contains("drawPhysicsOverlay"));

        let state_res = request(
            addr,
            &format!("GET /api/debug/v1/state HTTP/1.1\r\nHost: {addr}\r\n\r\n"),
        );
        assert!(state_res.starts_with("HTTP/1.1 200 OK\r\n"));
        let (_, body) = state_res.split_once("\r\n\r\n").unwrap();
        let state_json: Value = serde_json::from_str(body).unwrap();
        assert_eq!(state_json["rooms"][0]["physics"]["rays"][0]["hit"], "actor");
        assert_eq!(
            state_json["rooms"][0]["navigation"]["spawn_candidates"][0]["accepted"],
            true
        );
        assert_eq!(
            state_json["rooms"][0]["scripts"][0]["instances"][0]["active_states"][0]["flags"],
            7
        );
        assert_eq!(state_json["rooms"][0]["scripts_truncated"], true);

        let denied = request(
            addr,
            &format!("POST /api/debug/v1/state HTTP/1.1\r\nHost: {addr}\r\n\r\n"),
        );
        assert!(denied.starts_with("HTTP/1.1 405 Method Not Allowed\r\n"));
        assert!(denied.contains("Allow: GET\r\n"));

        let control_get_disabled = request(
            addr,
            &format!(
                "GET /api/debug/v1/rooms/00000000000000050000000000000008/control HTTP/1.1\r\nHost: {addr}\r\n\r\n"
            ),
        );
        assert!(control_get_disabled.starts_with("HTTP/1.1 404 Not Found\r\n"));

        let control_post_disabled = request(
            addr,
            &format!(
                "POST /api/debug/v1/rooms/00000000000000050000000000000008/control HTTP/1.1\r\n\
                 Host: {addr}\r\n\
                 Origin: http://{addr}\r\n\
                 Content-Type: application/json\r\n\
                 Content-Length: 18\r\n\r\n\
                 {{\"action\":\"pause\"}}"
            ),
        );
        assert!(control_post_disabled.starts_with("HTTP/1.1 405 Method Not Allowed\r\n"));
        assert!(control_post_disabled.contains("Allow: GET\r\n"));
    }

    #[test]
    fn serves_opt_in_room_control_post_and_rejects_untrusted_mutations() {
        use std::sync::Mutex;

        let calls = Arc::new(Mutex::new(Vec::<([u64; 2], RoomControlCommand)>::new()));
        let recorded = Arc::clone(&calls);
        let mut http = DebugHttp::bind_with_control(
            0,
            fixture_geometry(),
            || {
                Ok(Snapshot {
                    tick: 120,
                    rooms: Vec::new(),
                    truncated: false,
                    control_enabled: true,
                    timing: None,
                    ai_policy: None,
                    faults: Vec::new(),
                })
            },
            move |id, command| {
                recorded.lock().unwrap().push((id, command));
                match (id, command) {
                    ([0, 1], _) => Err(io::Error::new(io::ErrorKind::NotFound, "missing room")),
                    ([0, 2], _) => Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "unpaused room cannot step",
                    )),
                    ([0, 3], _) => Err(io::Error::new(
                        io::ErrorKind::PermissionDenied,
                        "control disabled",
                    )),
                    ([0, 4], _) => Err(io::Error::new(io::ErrorKind::WouldBlock, "worker busy")),
                    ([0, 5], _) => Err(io::Error::other("private worker failure")),
                    _ => Ok(RoomControlStatus {
                        paused: !matches!(command, RoomControlCommand::Resume),
                        pending_steps: match command {
                            RoomControlCommand::Step(steps) => steps,
                            _ => 0,
                        },
                        sim_tick: 120,
                        stepped_ticks: 3,
                        max_step_budget: MAX_DEBUG_STEP_BUDGET,
                    }),
                }
            },
        )
        .unwrap();
        let addr = http.addr();
        let room_hex = "00000000000000050000000000000008";
        let post = |path: &str, body: &str| {
            request(
                addr,
                &format!(
                    "POST {path} HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json; charset=utf-8\r\n\
                     Content-Length: {}\r\n\r\n\
                     {body}",
                    body.len()
                ),
            )
        };

        // Read-only GET on /control is strictly refused with 405 Allow: POST and never mutates.
        let get_control = request(
            addr,
            &format!("GET /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\nHost: {addr}\r\n\r\n"),
        );
        assert!(get_control.starts_with("HTTP/1.1 405 Method Not Allowed\r\n"));
        assert!(get_control.contains("Allow: POST\r\n"));
        assert!(calls.lock().unwrap().is_empty());

        for (payload, expected_cmd, expected_paused, expected_pending) in [
            ("{\"action\":\"pause\"}", RoomControlCommand::Pause, true, 0),
            (
                "{\"action\":\"step\"}",
                RoomControlCommand::Step(1),
                true,
                1,
            ),
            (
                "{\"action\":\"step\",\"steps\":5}",
                RoomControlCommand::Step(5),
                true,
                5,
            ),
            (
                "{\"action\":\"resume\"}",
                RoomControlCommand::Resume,
                false,
                0,
            ),
        ] {
            let response = post(&format!("/api/debug/v1/rooms/{room_hex}/control"), payload);
            assert!(
                response.starts_with("HTTP/1.1 200 OK\r\n"),
                "{payload}: {response}"
            );
            let (_, body) = response.split_once("\r\n\r\n").unwrap();
            let status: Value = serde_json::from_str(body).unwrap();
            assert_eq!(status["paused"], expected_paused);
            assert_eq!(status["pending_steps"], expected_pending);
            assert_eq!(status["sim_tick"], 120);
            assert_eq!(status["max_step_budget"], MAX_DEBUG_STEP_BUDGET);
            assert_eq!(calls.lock().unwrap().pop(), Some(([5, 8], expected_cmd)));
        }

        // Rejected mutation requests must never reach the control handler.
        let before_rejections = calls.lock().unwrap().len();
        let oversized_body = format!("{{\"action\":\"{}\"}}", "a".repeat(MAX_CONTROL_BODY_BYTES));
        let invalid_cases = [
            // Missing Origin header (CSRF guard)
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: 18\r\n\r\n\
                     {{\"action\":\"pause\"}}"
                ),
                "400",
            ),
            // Wrong Origin header
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://127.0.0.1:1\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: 18\r\n\r\n\
                     {{\"action\":\"pause\"}}"
                ),
                "400",
            ),
            // Wrong Content-Type
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: text/plain\r\n\
                     Content-Length: 18\r\n\r\n\
                     {{\"action\":\"pause\"}}"
                ),
                "415",
            ),
            // Missing Content-Length
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json\r\n\r\n"
                ),
                "400",
            ),
            // Oversized payload (> 256 bytes)
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: {}\r\n\r\n\
                     {oversized_body}",
                    oversized_body.len()
                ),
                "413",
            ),
            // Trailing bytes past Content-Length
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: 18\r\n\r\n\
                     {{\"action\":\"pause\"}}EXTRA"
                ),
                "400",
            ),
            // Unknown JSON fields / invalid action / out-of-range steps
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: 28\r\n\r\n\
                     {{\"action\":\"pause\",\"extra\":1}}"
                ),
                "400",
            ),
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: 28\r\n\r\n\
                     {{\"action\":\"pause\",\"steps\":1}}"
                ),
                "400",
            ),
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: 27\r\n\r\n\
                     {{\"action\":\"step\",\"steps\":0}}"
                ),
                "400",
            ),
            (
                format!(
                    "POST /api/debug/v1/rooms/{room_hex}/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: 28\r\n\r\n\
                     {{\"action\":\"step\",\"steps\":61}}"
                ),
                "400",
            ),
            // Malformed room hex ID
            (
                format!(
                    "POST /api/debug/v1/rooms/not-a-valid-hex-id/control HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: 18\r\n\r\n\
                     {{\"action\":\"pause\"}}"
                ),
                "400",
            ),
            // POST to read-only route
            (
                format!(
                    "POST /api/debug/v1/state HTTP/1.1\r\n\
                     Host: {addr}\r\n\
                     Origin: http://{addr}\r\n\
                     Content-Type: application/json\r\n\
                     Content-Length: 18\r\n\r\n\
                     {{\"action\":\"pause\"}}"
                ),
                "405",
            ),
        ];
        for (raw, expected_status) in invalid_cases {
            let response = request(addr, &raw);
            assert!(
                response.starts_with(&format!("HTTP/1.1 {expected_status} ")),
                "expected {expected_status}, got {response}"
            );
        }
        assert_eq!(calls.lock().unwrap().len(), before_rejections);

        // Verify error mapping and private diagnostic redaction.
        for (low, expected_status) in [
            (1_u64, "404 Not Found"),
            (2_u64, "400 Bad Request"),
            (3_u64, "403 Forbidden"),
            (4_u64, "429 Too Many Requests"),
            (5_u64, "503 Service Unavailable"),
        ] {
            let id_hex = format!("{:016X}{:016X}", 0_u64, low);
            let response = post(
                &format!("/api/debug/v1/rooms/{id_hex}/control"),
                "{\"action\":\"pause\"}",
            );
            assert!(
                response.starts_with(&format!("HTTP/1.1 {expected_status}\r\n")),
                "{id_hex}: {response}"
            );
            assert!(!response.contains("private worker failure"));
        }

        http.stop().unwrap();
    }
}
