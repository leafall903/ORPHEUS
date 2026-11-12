//! Optional, loopback-only HTTP view of native server state.

use std::{
    io::{self, Read, Write},
    net::{Ipv4Addr, SocketAddr, TcpListener},
    sync::{Arc, mpsc},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use crate::{
    debug_geometry::{DebugGeometry, GeometryRegion},
    game_debug::Snapshot,
    service_tcp::{PeerStream, PeerWorkers, accept},
};

const ACCEPT_POLL: Duration = Duration::from_millis(10);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_PEERS: usize = 8;
const MAX_REQUEST_BYTES: usize = 8192;
const MAX_REGION_QUERY_BYTES: usize = 128;
const HTML: &str = include_str!("../assets/inspector/index.html");
const CSS: &str = include_str!("../assets/inspector/debug.css");
const SCRIPT: &str = include_str!("../assets/inspector/debug.js");

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
            state: Box::new(state),
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

    let mut headers = [httparse::EMPTY_HEADER; 32];
    let mut request = httparse::Request::new(&mut headers);
    match request.parse(&buffer[..filled]) {
        Ok(httparse::Status::Complete(end)) if end == filled => {}
        Err(httparse::Error::TooManyHeaders) => {
            return error_response(&mut socket, "431 Request Header Fields Too Large");
        }
        _ => return error_response(&mut socket, "400 Bad Request"),
    }

    if !allowed_headers(request.headers, endpoint) {
        return error_response(&mut socket, "400 Bad Request");
    }
    if request.method != Some("GET") {
        return error_response(&mut socket, "405 Method Not Allowed");
    }
    let path = request.path.unwrap_or_default();
    let (route, query) = path
        .split_once('?')
        .map_or((path, None), |(route, query)| (route, Some(query)));
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
            let body =
                serde_json::to_vec(&endpoint.geometry.region(region)).map_err(io::Error::other)?;
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

fn allowed_headers(headers: &[httparse::Header<'_>], endpoint: &Endpoint) -> bool {
    let mut host_seen = false;
    let mut origin_seen = false;
    let mut content_length_seen = false;
    for header in headers {
        if header.name.eq_ignore_ascii_case("host") {
            if host_seen || header.value != endpoint.host.as_bytes() {
                return false;
            }
            host_seen = true;
        } else if header.name.eq_ignore_ascii_case("origin") {
            if origin_seen || header.value != endpoint.origin.as_bytes() {
                return false;
            }
            origin_seen = true;
        } else if header.name.eq_ignore_ascii_case("content-length") {
            if content_length_seen || header.value != b"0" {
                return false;
            }
            content_length_seen = true;
        } else if header.name.eq_ignore_ascii_case("transfer-encoding") {
            return false;
        }
    }
    host_seen
}

fn error_response(socket: &mut PeerStream, status: &str) -> io::Result<()> {
    respond(
        socket,
        status,
        "text/plain; charset=utf-8",
        status.as_bytes(),
    )
}

fn respond(
    socket: &mut PeerStream,
    status: &str,
    content_type: &str,
    body: &[u8],
) -> io::Result<()> {
    let allow = if status == "405 Method Not Allowed" {
        "Allow: GET\r\n"
    } else {
        ""
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
                timing: None,
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
}
