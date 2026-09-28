/**
 * Plays a camera's live video from go2rtc: its H.264 stream, repackaged as
 * fragmented MP4 and sent over a WebSocket, fed to a <video> through Media
 * Source Extensions. Only the WebSocket leaves the browser, so it passes any
 * HTTP proxy that passes WebSockets.
 *
 * Protocol (go2rtc /api/ws): once open, the client sends
 * {type: 'mse', value: '<codecs it can play>'}; go2rtc answers
 * {type: 'mse', value: '<mime type>'} and then sends binary MP4 fragments,
 * or {type: 'error', value} when it cannot.
 */

// Video only: the lane has nothing worth hearing. H.264 first, the codec of
// almost every sub-stream; H.265 plays in few browsers.
const CODECS = ['avc1.640029', 'avc1.64002A', 'avc1.640033', 'hvc1.1.6.L153.B0'];
// Frames kept behind the live edge; older ones are dropped from the buffer
const KEEP_SECONDS = 5;
// Further behind than this (a hidden tab, a slow link), jump to the live edge
const MAX_LAG_SECONDS = 2;
// No video within this long counts as a failure
const FIRST_DATA_TIMEOUT_MS = 10000;
// No new data for this long: the stream is stuck
const STALL_MS = 5000;

type MediaSourceCtor = typeof MediaSource;

interface LiveStreamHandlers {
  /** The first video arrived. */
  onPlaying?: () => void;
  /** The stream failed or closed; the caller shows something else. */
  onFail: (reason: string) => void;
  /** true while no data has arrived for a few seconds, false once it resumes. */
  onStall?: (stalled: boolean) => void;
}

function mediaSourceCtor(): { ctor: MediaSourceCtor; managed: boolean } | null {
  if (typeof window === 'undefined') return null;
  const managed = (window as unknown as { ManagedMediaSource?: MediaSourceCtor }).ManagedMediaSource;
  if (managed) return { ctor: managed, managed: true };
  if ('MediaSource' in window) return { ctor: window.MediaSource, managed: false };
  return null;
}

/** Whether this browser can play live video at all (iPhones before iOS 17 cannot). */
export function liveStreamSupported(): boolean {
  const ms = mediaSourceCtor();
  return !!ms && typeof WebSocket !== 'undefined'
    && CODECS.some((c) => ms.ctor.isTypeSupported(`video/mp4; codecs="${c}"`));
}

/** A path on this site becomes a ws(s):// URL on this site. */
export function webSocketUrl(url: string): string {
  if (/^wss?:\/\//.test(url)) return url;
  if (/^https?:\/\//.test(url)) return url.replace(/^http/, 'ws');
  const { protocol, host } = window.location;
  return `${protocol === 'https:' ? 'wss:' : 'ws:'}//${host}${url.startsWith('/') ? '' : '/'}${url}`;
}

/** Start playing into video. Returns a function that stops and releases everything. */
export function playLiveStream(video: HTMLVideoElement, url: string, handlers: LiveStreamHandlers): () => void {
  const ms = mediaSourceCtor();
  let stopped = false;
  let started = false;
  let stalled = false;
  let lastData = Date.now();
  let ws: WebSocket | null = null;
  let objectUrl = '';
  let sb: SourceBuffer | null = null;
  const queue: ArrayBuffer[] = [];

  // Timeouts and intervals share one pool of ids, and clearTimeout clears either
  const timers: number[] = [];

  const stop = () => {
    if (stopped) return;
    stopped = true;
    timers.forEach((id) => window.clearTimeout(id));
    if (ws) {
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      ws.close();
    }
    video.removeAttribute('src');
    video.srcObject = null;
    video.load();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  };

  const fail = (reason: string) => {
    if (stopped) return;
    stop();
    handlers.onFail(reason);
  };

  if (!ms || !liveStreamSupported()) {
    // Reported asynchronously, like every other outcome
    timers.push(window.setTimeout(() => fail('unsupported'), 0));
    return stop;
  }

  const source = new ms.ctor();
  if (ms.managed) {
    // Safari's ManagedMediaSource plays only with remote playback off
    video.disableRemotePlayback = true;
    video.srcObject = source as unknown as MediaProvider;
  } else {
    objectUrl = URL.createObjectURL(source);
    video.src = objectUrl;
  }
  video.muted = true;
  video.playsInline = true;

  const pump = () => {
    if (!sb || sb.updating) return;
    if (queue.length) {
      const total = queue.reduce((n, b) => n + b.byteLength, 0);
      const data = new Uint8Array(total);
      let offset = 0;
      for (const chunk of queue.splice(0)) {
        data.set(new Uint8Array(chunk), offset);
        offset += chunk.byteLength;
      }
      try {
        sb.appendBuffer(data);
      } catch (e) {
        fail(`append: ${(e as Error).name}`);
      }
      return;
    }
    const { buffered } = sb;
    if (!buffered.length) return;
    const end = buffered.end(buffered.length - 1);
    const start = buffered.start(0);
    if (end - start > KEEP_SECONDS + 1) {
      try {
        sb.remove(start, end - KEEP_SECONDS);
      } catch {
        // Removal is housekeeping; the next update tries again
      }
    }
    if (end - video.currentTime > MAX_LAG_SECONDS || video.currentTime < start) {
      video.currentTime = Math.max(start, end - 0.3);
    }
  };

  let wsOpen = false;
  let sourceOpen = false;
  const codecs = CODECS.filter((c) => ms.ctor.isTypeSupported(`video/mp4; codecs="${c}"`)).join();
  const request = () => {
    if (wsOpen && sourceOpen && ws) ws.send(JSON.stringify({ type: 'mse', value: codecs }));
  };

  source.addEventListener('sourceopen', () => {
    sourceOpen = true;
    request();
  }, { once: true });

  try {
    ws = new WebSocket(webSocketUrl(url));
  } catch (e) {
    timers.push(window.setTimeout(() => fail(`websocket: ${(e as Error).message}`), 0));
    return stop;
  }
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {
    wsOpen = true;
    request();
  };
  // A refused authorization closes the socket before it opens: the browser does
  // not say why, and the caller falls back to frames either way.
  ws.onerror = () => fail('websocket error');
  ws.onclose = () => fail('websocket closed');
  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      let msg: { type?: string; value?: string };
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === 'mse' && msg.value && !sb) {
        try {
          sb = source.addSourceBuffer(msg.value);
        } catch (e) {
          fail(`codec ${msg.value}: ${(e as Error).name}`);
          return;
        }
        sb.mode = 'segments';
        sb.addEventListener('updateend', pump);
      } else if (msg.type === 'error') {
        fail(msg.value || 'stream error');
      }
      return;
    }
    lastData = Date.now();
    if (stalled) {
      stalled = false;
      handlers.onStall?.(false);
    }
    queue.push(ev.data as ArrayBuffer);
    pump();
    if (!started) {
      started = true;
      video.play().catch(() => {
        // Muted autoplay is allowed everywhere; a refusal leaves the first frame showing
      });
      handlers.onPlaying?.();
    }
  };

  timers.push(window.setTimeout(() => {
    if (!started) fail('no video');
  }, FIRST_DATA_TIMEOUT_MS));
  timers.push(window.setInterval(() => {
    if (started && !stalled && Date.now() - lastData > STALL_MS) {
      stalled = true;
      handlers.onStall?.(true);
    }
  }, 1000));

  return stop;
}
