//! Worker event loop, UDP I/O polling, and tick scheduling.

use super::*;

pub(super) const GAME_SOCKET: Token = Token(0);
pub(super) const REQUESTS: Token = Token(1);
pub(super) const RECEIVE_BATCH: usize = 64;
pub(super) const INPUT_PER_PEER: usize = 16;
pub(super) const INPUT_PER_TURN: usize = 64;

pub(super) fn leave_message() -> Message {
    Message {
        id: 20304,
        fields: Fields::from([(0x78, Value::Unsigned(0)), (0x79, Value::Bool(false))]),
    }
}

pub(super) fn wall_nanoseconds() -> u64 {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    u64::try_from(now.as_nanos()).unwrap_or(u64::MAX)
}

pub(super) fn send(socket: &UdpSocket, outgoing: Outgoing) -> io::Result<u32> {
    let count = socket.send_to(&outgoing.datagram, outgoing.address)?;
    if count != outgoing.datagram.len() {
        return Err(io::Error::new(
            io::ErrorKind::WriteZero,
            "partial game datagram",
        ));
    }
    Ok(outgoing.sequence)
}

pub(super) struct WorkerOptions {
    pub(super) collect_timing: bool,
    #[cfg(any(test, feature = "inspector"))]
    pub(super) debug_control: bool,
}

pub(super) fn run(
    socket: UdpSocket,
    mut poll: Poll,
    requests: mpsc::Receiver<Request>,
    stopping: Arc<AtomicBool>,
    schemas: Arc<Schemas>,
    ready: mpsc::SyncSender<io::Result<()>>,
    options: WorkerOptions,
) -> io::Result<()> {
    let patrol = match PatrolScript::load() {
        Ok(patrol) => patrol,
        Err(error) => {
            let _ = ready.send(Err(error));
            return Ok(());
        }
    };
    let mut state = State::new(schemas, patrol);
    state.timing = options.collect_timing.then(WorkerTiming::new);
    #[cfg(any(test, feature = "inspector"))]
    {
        state.debug_control_enabled = options.debug_control;
    }
    if ready.send(Ok(())).is_err() {
        return Ok(());
    }
    let start = Instant::now();
    let mut due = Duration::ZERO;
    let mut events = Events::with_capacity(2);
    // An oversized datagram must never be accepted as a truncated valid one.
    let mut buffer = [0; 65_536];
    let mut socket_ready = false;
    while !stopping.load(Ordering::Acquire) {
        let mut handled = 0;
        for request in requests.try_iter().take(RECEIVE_BATCH) {
            state.handle_request(request, start.elapsed())?;
            handled += 1;
        }
        if socket_ready {
            for _ in 0..RECEIVE_BATCH {
                match socket.recv_from(&mut buffer) {
                    Ok((count, address)) => {
                        state.receive(&socket, &buffer[..count], address, start.elapsed())?;
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        socket_ready = false;
                        break;
                    }
                    Err(error)
                        if matches!(
                            error.kind(),
                            io::ErrorKind::Interrupted
                                | io::ErrorKind::ConnectionReset
                                | io::ErrorKind::ConnectionRefused
                        ) =>
                    {
                        continue;
                    }
                    Err(error) => return Err(error),
                }
            }
        }
        let input_ready = state.advance_input(start.elapsed())?;
        let now = start.elapsed();
        if now >= due {
            let scheduled = due;
            let (next, missed) = next_tick_after(scheduled, now)
                .ok_or_else(|| io::Error::other("game tick clock exhausted"))?;
            due = next;
            let sample = state.timing.is_some() && !state.clock.draining() && state.world_started();
            let began = sample.then(Instant::now);
            state.tick(&socket, now, missed)?;
            if let (Some(timing), Some(began)) = (&mut state.timing, began) {
                timing.record_tick(began.elapsed(), now - scheduled, missed);
            }
        }
        let timeout = if socket_ready || input_ready || handled == RECEIVE_BATCH {
            Duration::ZERO
        } else {
            due.saturating_sub(start.elapsed())
        };
        match poll.poll(&mut events, Some(timeout)) {
            Ok(()) => {
                socket_ready |= events.iter().any(|event| event.token() == GAME_SOCKET);
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(())
}
