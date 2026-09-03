const libraryListEl = document.getElementById("library-list");
const searchBoxEl = document.getElementById("search-box");
const queueBtn = document.getElementById("queue-btn");
const lyricsStatusEl = document.getElementById("lyrics-status");
const lyricsLinesEl = document.getElementById("lyrics-lines");
const trackTitleEl = document.getElementById("track-title");
const trackArtistEl = document.getElementById("track-artist");
const playBtn = document.getElementById("play-btn");
const prevBtn = document.getElementById("prev-btn");
const nextBtn = document.getElementById("next-btn");
const tvModeBtn = document.getElementById("tv-mode-btn");
const editLyricsBtn = document.getElementById("edit-lyrics-btn");
const lyricsEditOverlay = document.getElementById("lyrics-edit-overlay");
const lyricsEditTextarea = document.getElementById("lyrics-edit-textarea");
const lyricsEditCancel = document.getElementById("lyrics-edit-cancel");
const lyricsEditSave = document.getElementById("lyrics-edit-save");
const partyInfoEl = document.getElementById("party-info");
const partyLinkEl = document.getElementById("party-link");
const partyQrEl = document.getElementById("party-qr");
const speedSelectEl = document.getElementById("speed-select");
const albumArtEl = document.getElementById("album-art");
const reactionOverlayEl = document.getElementById("reaction-overlay");
const recapBtn = document.getElementById("recap-btn");
const recapOverlay = document.getElementById("recap-overlay");
const recapListEl = document.getElementById("recap-list");
const recapClose = document.getElementById("recap-close");
const helpBtn = document.getElementById("help-btn");
const tourTooltipEl = document.getElementById("tour-tooltip");
const tourTextEl = document.getElementById("tour-text");
const tourNextBtn = document.getElementById("tour-next");
const tourSkipBtn = document.getElementById("tour-skip");
const refreshLibraryBtn = document.getElementById("refresh-library-btn");
const offsetIndicatorEl = document.getElementById("lyric-offset");
const hostLockEl = document.getElementById("host-lock");
const hostUnlockBtn = document.getElementById("host-unlock-btn");

let lastReactionSeq = 0;
// Separate from lastReactionSeq: on a fresh server nobody has reacted yet, so
// the sequence sits at 0 through any number of polls. Using it as the "have we
// synced yet" flag swallowed the first reactions of the night, every night.
let reactionsPrimed = false;
let currentOffsetSec = 0;
let rotationEnabled = true;

let ytPlayer = null;
let playerReady = false;
// currentTracks/currentIndex = a local cache of the server-authoritative queue
// (GET/POST /api/queue*) -- shared across every open tab/device, including
// guests on /remote. browsingTracks = whatever list is currently shown in the
// sidebar (a playlist, search results, etc) -- separate so just looking at a
// playlist doesn't change what Next/Prev would play.
let currentTracks = [];
let currentIndex = -1;
let browsingTracks = [];
let viewingQueue = false;
let lyricLines = [];
let lyricEls = [];
let syncTimer = null;
let lastActiveIdx = -1;
let allTracksIndex = [];

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const error = new Error(body.detail || `Request failed: ${path}`);
    error.status = res.status;
    throw error;
  }
  return res.json();
}

// Host controls are PIN-gated so guests can't skip or clear the queue. This
// page gets the PIN handed to it automatically when it's running on the same
// machine as the server; anywhere else (a TV browser on the LAN) it asks once.
let hostPin = null;

function readStoredPin() {
  try {
    return localStorage.getItem("karaoke_host_pin");
  } catch (err) {
    return null;
  }
}

function storePin(pin) {
  hostPin = pin;
  try {
    localStorage.setItem("karaoke_host_pin", pin);
  } catch (err) {
    // ignore -- storage unavailable, PIN just won't survive a refresh
  }
}

async function loadHostPin() {
  try {
    storePin((await api("/api/host-pin")).pin);
  } catch (err) {
    // Not the server's own machine. A PIN from an earlier session may be
    // stale (it's regenerated per run) -- we find out on the first 403.
    hostPin = readStoredPin();
  }
  updateHostLockUi();
}

const hostPinReady = loadHostPin();

function updateHostLockUi() {
  if (hostLockEl) hostLockEl.hidden = Boolean(hostPin);
}

function promptForHostPin() {
  const entered = window.prompt(
    "Host controls are locked. Enter the host PIN shown in the server window:"
  );
  if (!entered) return false;
  storePin(entered.trim());
  updateHostLockUi();
  return true;
}

function requestOptions(body) {
  const headers = { "Content-Type": "application/json" };
  if (hostPin) headers["X-Host-Pin"] = hostPin;
  return { method: "POST", headers, body: JSON.stringify(body) };
}

async function postJson(path, body) {
  await hostPinReady;
  try {
    return await api(path, requestOptions(body));
  } catch (err) {
    if (err.status !== 403) throw err;
    hostPin = null;
    updateHostLockUi();
    if (!promptForHostPin()) throw err;
    return api(path, requestOptions(body));
  }
}

function applyQueueState(state) {
  // Track by uid, not videoId: advancing between two copies of the same song
  // is still a track change and has to reload the player.
  const previousUid = currentTracks[currentIndex] ? currentTracks[currentIndex].uid : null;
  currentTracks = state.tracks;
  currentIndex = state.currentIndex;
  rotationEnabled = state.rotation !== false;
  updateQueueBadge();

  if (viewingQueue) {
    showQueue();
  } else {
    highlightPlayingTrack();
  }

  const nowPlayingUid = currentTracks[currentIndex] ? currentTracks[currentIndex].uid : null;
  if (nowPlayingUid !== previousUid) {
    loadCurrentTrackIntoPlayer();
  }
}

async function refreshQueue() {
  try {
    applyQueueState(await api("/api/queue"));
  } catch (err) {
    // ignore transient poll failures
  }
}

async function loadSearchIndex(refresh = false) {
  try {
    if (refresh) searchBoxEl.placeholder = "Refreshing library…";
    allTracksIndex = await api(refresh ? "/api/library/all?refresh=1" : "/api/library/all");
    searchBoxEl.disabled = false;
    searchBoxEl.placeholder = "Search your library…";
  } catch (err) {
    searchBoxEl.placeholder = "Search unavailable";
  }
}

async function runSearch(query) {
  const q = query.trim();
  if (!q) {
    loadLibrary();
    return;
  }
  viewingQueue = false;
  const lower = q.toLowerCase();
  const libraryMatches = allTracksIndex.filter(
    (t) => t.title.toLowerCase().includes(lower) || t.artist.toLowerCase().includes(lower)
  );

  browsingTracks = libraryMatches;
  libraryListEl.innerHTML = "";

  const back = document.createElement("div");
  back.className = "back-link";
  back.textContent = "← Back to library";
  back.onclick = () => {
    searchBoxEl.value = "";
    loadLibrary();
  };
  libraryListEl.appendChild(back);

  const libHeading = document.createElement("h1");
  libHeading.textContent = `Your Library (${libraryMatches.length})`;
  libraryListEl.appendChild(libHeading);
  renderTrackList(libraryMatches, "browse", 0);

  if (libraryMatches.length < 5) {
    const catalogHeading = document.createElement("h1");
    catalogHeading.style.marginTop = "16px";
    catalogHeading.textContent = "Searching YouTube Music…";
    libraryListEl.appendChild(catalogHeading);

    try {
      const catalogMatches = await api(`/api/search/catalog?q=${encodeURIComponent(q)}`);
      browsingTracks = [...libraryMatches, ...catalogMatches];
      catalogHeading.textContent = `From YouTube Music (${catalogMatches.length})`;
      renderTrackList(catalogMatches, "browse", libraryMatches.length);
    } catch (err) {
      catalogHeading.textContent = "Couldn't search YouTube Music";
    }
  }
}

async function loadLibrary() {
  viewingQueue = false;
  libraryListEl.innerHTML = "";

  try {
    const [playlists, liked] = await Promise.all([
      api("/api/library/playlists"),
      api("/api/library/liked"),
    ]);

    const likedItem = document.createElement("div");
    likedItem.className = "lib-item";
    likedItem.textContent = "Liked Songs";
    const likedCount = document.createElement("span");
    likedCount.className = "count";
    likedCount.textContent = liked.length;
    likedItem.appendChild(likedCount);
    likedItem.onclick = () => showTracks("Liked Songs", liked);
    libraryListEl.appendChild(likedItem);

    for (const pl of playlists) {
      const item = document.createElement("div");
      item.className = "lib-item";
      item.textContent = pl.title;
      if (pl.count != null) {
        const count = document.createElement("span");
        count.className = "count";
        count.textContent = pl.count;
        item.appendChild(count);
      }
      item.onclick = async () => {
        const data = await api(`/api/playlist/${encodeURIComponent(pl.playlistId)}`);
        showTracks(data.title || pl.title, data.tracks);
      };
      libraryListEl.appendChild(item);
    }
  } catch (err) {
    libraryListEl.innerHTML = `<div style="color:#f66;font-size:13px;">${err.message}</div>`;
  }
}

function showTracks(heading, tracks) {
  viewingQueue = false;
  browsingTracks = tracks;
  libraryListEl.innerHTML = "";

  const back = document.createElement("div");
  back.className = "back-link";
  back.textContent = "← Back to library";
  back.onclick = () => {
    searchBoxEl.value = "";
    loadLibrary();
  };
  libraryListEl.appendChild(back);

  const title = document.createElement("h1");
  title.textContent = heading;
  libraryListEl.appendChild(title);

  const shuffleBtn = document.createElement("button");
  shuffleBtn.id = "shuffle-btn";
  shuffleBtn.textContent = "🔀 Shuffle All";
  shuffleBtn.onclick = shuffleAndPlay;
  libraryListEl.appendChild(shuffleBtn);

  renderTrackList(tracks, "browse");
}

function showQueue() {
  viewingQueue = true;
  libraryListEl.innerHTML = "";

  const back = document.createElement("div");
  back.className = "back-link";
  back.textContent = "← Back to library";
  back.onclick = () => {
    searchBoxEl.value = "";
    loadLibrary();
  };
  libraryListEl.appendChild(back);

  const title = document.createElement("h1");
  title.textContent = `Queue (${currentTracks.length})`;
  libraryListEl.appendChild(title);

  const rotationBtn = document.createElement("button");
  rotationBtn.id = "rotation-btn";
  rotationBtn.classList.toggle("on", rotationEnabled);
  rotationBtn.textContent = rotationEnabled
    ? "🔁 Fair rotation: on"
    : "🔁 Fair rotation: off";
  rotationBtn.title =
    "When on, someone's 2nd request waits until everyone else has had a turn";
  rotationBtn.onclick = async () => {
    applyQueueState(await postJson("/api/queue/rotation", { enabled: !rotationEnabled }));
  };
  libraryListEl.appendChild(rotationBtn);

  if (currentTracks.length > 1) {
    const shuffleBtn = document.createElement("button");
    shuffleBtn.id = "shuffle-btn";
    shuffleBtn.textContent = "🔀 Shuffle Queue";
    shuffleBtn.onclick = shuffleQueue;
    libraryListEl.appendChild(shuffleBtn);
  }

  if (currentTracks.length > 0) {
    const clearBtn = document.createElement("button");
    clearBtn.id = "clear-queue-btn";
    clearBtn.textContent = "🗑️ Clear Queue";
    clearBtn.onclick = clearQueue;
    libraryListEl.appendChild(clearBtn);
  }

  renderTrackList(currentTracks, "queue");
}

function renderTrackList(tracks, mode, startIndex = 0) {
  tracks.forEach((track, i) => {
    const idx = startIndex + i;
    const item = document.createElement("div");
    item.className = "track-item";
    item.dataset.videoId = track.videoId;
    if (track.uid) item.dataset.uid = track.uid;

    const main = document.createElement("div");
    main.className = "t-main";
    const textWrap = document.createElement("span");
    const t = document.createElement("span");
    t.textContent = track.title;
    textWrap.appendChild(t);
    if (track.addedBy) {
      const by = document.createElement("span");
      by.style.cssText = "color:#6f6f7c;font-size:11px;margin-left:6px;";
      by.textContent = `+ ${track.addedBy}`;
      textWrap.appendChild(by);
    }
    const a = document.createElement("span");
    a.className = "t-artist";
    a.textContent = track.artist;
    main.appendChild(textWrap);
    main.appendChild(a);
    item.appendChild(main);

    if (mode === "queue") {
      main.onclick = () => playTrack(track.uid);
      if (idx !== currentIndex) {
        const removeBtn = document.createElement("button");
        removeBtn.className = "t-action";
        removeBtn.title = "Remove from queue";
        removeBtn.textContent = "✕";
        removeBtn.onclick = (event) => {
          event.stopPropagation();
          removeFromQueue(track.uid);
        };
        item.appendChild(removeBtn);
      }
    } else {
      main.onclick = () => playFromBrowsing(idx);
      const addBtn = document.createElement("button");
      addBtn.className = "t-action";
      addBtn.title = "Add to queue";
      addBtn.textContent = "+";
      addBtn.onclick = (event) => {
        event.stopPropagation();
        addToQueue(idx);
      };
      item.appendChild(addBtn);
    }

    libraryListEl.appendChild(item);
  });

  highlightPlayingTrack();
}

function updateQueueBadge() {
  queueBtn.textContent = `🎵 Queue (${currentTracks.length})`;
}

async function playFromBrowsing(idx) {
  applyQueueState(await postJson("/api/queue/set", { tracks: browsingTracks, startIndex: idx }));
}

async function addToQueue(idx) {
  applyQueueState(await postJson("/api/queue/add", { track: browsingTracks[idx] }));
}

async function removeFromQueue(uid) {
  applyQueueState(await postJson("/api/queue/remove", { uid }));
}

async function shuffleAndPlay() {
  const shuffled = [...browsingTracks];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  applyQueueState(await postJson("/api/queue/set", { tracks: shuffled, startIndex: 0 }));
}

async function shuffleQueue() {
  applyQueueState(await api("/api/queue/shuffle", { method: "POST" }));
}

async function clearQueue() {
  if (!confirm("Clear the entire queue? This also stops the current track.")) return;
  applyQueueState(await postJson("/api/queue/set", { tracks: [], startIndex: -1 }));
}

function highlightPlayingTrack() {
  const playing = currentTracks[currentIndex] || null;
  document.querySelectorAll(".track-item").forEach((el) => {
    // Queue rows match on uid so a song queued twice only lights up the copy
    // that's actually playing; browse rows only have a videoId to go on.
    const match = el.dataset.uid
      ? playing && el.dataset.uid === playing.uid
      : playing && el.dataset.videoId === playing.videoId;
    el.classList.toggle("playing", Boolean(match));
  });
}

async function playTrack(uid) {
  if (!uid) return;
  applyQueueState(await postJson("/api/queue/advance", { uid }));
}

function loadCurrentTrackIntoPlayer() {
  const track = currentTracks[currentIndex];
  if (!track) {
    trackTitleEl.textContent = "";
    trackArtistEl.textContent = "";
    albumArtEl.hidden = true;
    resetLyrics("Pick a track from the library to start.");
    if (playerReady) ytPlayer.stopVideo();
    return;
  }

  trackTitleEl.textContent = track.title;
  trackArtistEl.textContent = track.artist;

  if (track.thumbnail) {
    albumArtEl.src = track.thumbnail;
    albumArtEl.hidden = false;
  } else {
    albumArtEl.hidden = true;
  }

  resetLyrics("Loading lyrics…");
  loadLyrics(track);

  if (playerReady && track.videoId) {
    ytPlayer.loadVideoById(track.videoId);
  }
}

function updateOffsetIndicator() {
  if (!offsetIndicatorEl) return;
  if (!currentOffsetSec) {
    offsetIndicatorEl.hidden = true;
    return;
  }
  const ms = Math.round(currentOffsetSec * 1000);
  offsetIndicatorEl.textContent = (ms > 0 ? "lyrics +" : "lyrics ") + ms + "ms";
  offsetIndicatorEl.hidden = false;
}

async function nudgeLyricOffset(deltaSec) {
  const track = currentTracks[currentIndex];
  if (!track || !track.videoId) return;

  currentOffsetSec = Math.round((currentOffsetSec + deltaSec) * 1000) / 1000;
  updateOffsetIndicator();
  lastActiveIdx = -1;

  try {
    await postJson("/api/lyrics/offset", {
      video_id: track.videoId,
      offset_sec: currentOffsetSec,
    });
  } catch (err) {
    // The nudge still applies for this play-through; it just won't be
    // remembered next time.
  }
}

function resetLyrics(status) {
  currentOffsetSec = 0;
  updateOffsetIndicator();
  lyricsStatusEl.textContent = status;
  lyricsStatusEl.style.display = "block";
  lyricsLinesEl.innerHTML = "";
  lyricLines = [];
  lyricEls = [];
  lastActiveIdx = -1;
}

async function loadLyrics(track) {
  try {
    const params = new URLSearchParams({ artist: track.artist, title: track.title });
    if (track.durationSeconds) params.set("duration", track.durationSeconds);
    if (track.videoId) params.set("video_id", track.videoId);
    const data = await api(`/api/lyrics?${params.toString()}`);

    currentOffsetSec = data.offsetSec || 0;
    updateOffsetIndicator();

    if (!data.lines || data.lines.length === 0) {
      resetLyrics("No lyrics found for this track.");
      return;
    }

    lyricsStatusEl.style.display = "none";
    lyricLines = data.lines;
    lyricEls = [];
    lyricsLinesEl.innerHTML = "";

    for (const line of lyricLines) {
      const div = document.createElement("div");
      div.className = "lyric-line";
      if (line.words) {
        line.words.forEach((word, wi) => {
          const span = document.createElement("span");
          span.className = "lyric-word";
          span.textContent = word.text + (wi < line.words.length - 1 ? " " : "");
          div.appendChild(span);
        });
      } else {
        div.textContent = line.text || "♪";
      }
      lyricsLinesEl.appendChild(div);
      lyricEls.push(div);
    }

    if (!data.synced) {
      const note = document.createElement("div");
      note.style.cssText = "color:#6f6f7c;font-size:13px;margin-top:8px;";
      note.textContent = "(plain lyrics only — not time-synced)";
      lyricsLinesEl.appendChild(note);
    } else if (data.source === "captions") {
      const note = document.createElement("div");
      note.style.cssText = "color:#6f6f7c;font-size:13px;margin-top:8px;";
      note.textContent = data.autoGenerated
        ? "(from auto-generated video captions — may have transcription errors)"
        : "(from video captions)";
      lyricsLinesEl.appendChild(note);
    } else if (data.source === "manual") {
      const note = document.createElement("div");
      note.style.cssText = "color:#6f6f7c;font-size:13px;margin-top:8px;";
      note.textContent = "(your manual lyrics)";
      lyricsLinesEl.appendChild(note);
    }
  } catch (err) {
    resetLyrics(`Couldn't load lyrics: ${err.message}`);
  }
}

function syncLyrics() {
  if (!playerReady || lyricLines.length === 0) return;
  const rawTime = ytPlayer.getCurrentTime();
  if (typeof rawTime !== "number") return;
  // A positive offset holds each line back, for sources timed against a
  // different master than the one YouTube is serving.
  const t = rawTime - currentOffsetSec;

  let activeIdx = -1;
  for (let i = 0; i < lyricLines.length; i++) {
    if (lyricLines[i].time_sec == null) continue;
    if (lyricLines[i].time_sec <= t) activeIdx = i;
    else break;
  }

  lyricEls.forEach((el, i) => {
    el.classList.toggle("current", i === activeIdx);
    el.classList.toggle("past", activeIdx !== -1 && i < activeIdx);
  });

  if (activeIdx !== -1 && lyricLines[activeIdx].words) {
    const words = lyricLines[activeIdx].words;
    const wordSpans = lyricEls[activeIdx].querySelectorAll(".lyric-word");
    let activeWordIdx = -1;
    for (let i = 0; i < words.length; i++) {
      if (words[i].time_sec <= t) activeWordIdx = i;
      else break;
    }
    wordSpans.forEach((span, i) => {
      span.classList.toggle("sung", i <= activeWordIdx);
    });
  }

  if (activeIdx !== -1 && activeIdx !== lastActiveIdx && lyricEls[activeIdx]) {
    lyricEls[activeIdx].scrollIntoView({ behavior: "smooth", block: "center" });
  }
  lastActiveIdx = activeIdx;
}

async function playNext() {
  applyQueueState(await postJson("/api/queue/next", {}));
}

async function playPrev() {
  applyQueueState(await postJson("/api/queue/prev", {}));
}

function togglePlayPause() {
  if (!playerReady) return;
  const state = ytPlayer.getPlayerState();
  if (state === YT.PlayerState.PLAYING) {
    ytPlayer.pauseVideo();
  } else {
    ytPlayer.playVideo();
  }
}

playBtn.onclick = togglePlayPause;
nextBtn.onclick = playNext;
prevBtn.onclick = playPrev;

searchBoxEl.oninput = () => runSearch(searchBoxEl.value);
queueBtn.onclick = showQueue;

function setTvMode(on) {
  document.body.classList.toggle("tv-mode", on);
}

function toggleTvMode() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
    setTvMode(true);
  } else {
    document.exitFullscreen().catch(() => {});
  }
}

tvModeBtn.onclick = toggleTvMode;

async function loadPartyInfo() {
  try {
    const data = await api("/api/party-info");
    partyLinkEl.textContent = data.lan_url;
    partyLinkEl.href = data.lan_url;
    if (data.qr_png_base64) {
      partyQrEl.src = `data:image/png;base64,${data.qr_png_base64}`;
    }
    partyInfoEl.hidden = false;
  } catch (err) {
    // optional -- ignore failures
  }
}

function populateSpeedOptions() {
  const rates = ytPlayer.getAvailablePlaybackRates();
  speedSelectEl.innerHTML = "";
  rates.forEach((rate) => {
    const opt = document.createElement("option");
    opt.value = rate;
    opt.textContent = `${rate}x`;
    if (rate === 1) opt.selected = true;
    speedSelectEl.appendChild(opt);
  });
}

speedSelectEl.onchange = () => {
  if (playerReady) ytPlayer.setPlaybackRate(Number(speedSelectEl.value));
};

async function pollReactions() {
  try {
    const data = await api(`/api/react?since=${lastReactionSeq}`);
    lastReactionSeq = data.latestSeq;
    // Don't replay reactions from before this page loaded -- but from then on,
    // show every one, not just the newest in each polling window.
    if (!reactionsPrimed) {
      reactionsPrimed = true;
      return;
    }
    for (const reaction of data.reactions) {
      spawnReactionBurst(reaction.emoji, reaction.name);
    }
  } catch (err) {
    // ignore transient poll failures
  }
}

function spawnReactionBurst(emoji, name) {
  const el = document.createElement("div");
  el.className = "reaction-burst";
  el.textContent = emoji;
  if (name) {
    const tag = document.createElement("span");
    tag.className = "reaction-name";
    tag.textContent = name;
    el.appendChild(tag);
  }
  // Spread wider than the old single-slot version -- several of these can now
  // be on screen at once.
  el.style.left = `${15 + Math.random() * 70}%`;
  reactionOverlayEl.appendChild(el);
  el.addEventListener("animationend", () => el.remove());
}

function showRecap() {
  const counts = new Map();
  for (const track of currentTracks.slice(0, currentIndex + 1)) {
    const name = track.addedBy || "Host";
    counts.set(name, (counts.get(name) || 0) + 1);
  }

  recapListEl.innerHTML = "";
  if (counts.size === 0) {
    const empty = document.createElement("div");
    empty.style.color = "#8a8a99";
    empty.textContent = "No songs played yet tonight.";
    recapListEl.appendChild(empty);
  } else {
    [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .forEach(([name, count]) => {
        const row = document.createElement("div");
        row.className = "recap-row";
        const nameEl = document.createElement("span");
        nameEl.textContent = name;
        const countEl = document.createElement("span");
        countEl.className = "recap-count";
        countEl.textContent = `${count} song${count === 1 ? "" : "s"}`;
        row.appendChild(nameEl);
        row.appendChild(countEl);
        recapListEl.appendChild(row);
      });
  }
  recapOverlay.hidden = false;
}

recapBtn.onclick = showRecap;
recapClose.onclick = () => {
  recapOverlay.hidden = true;
};

function linesToText(lines) {
  if (!lines || lines.length === 0) return "";
  const synced = lines.some((l) => l.time_sec != null);
  if (!synced) return lines.map((l) => l.text).join("\n");
  return lines
    .map((l) => {
      if (l.time_sec == null) return l.text;
      const mm = String(Math.floor(l.time_sec / 60)).padStart(2, "0");
      const ss = (l.time_sec % 60).toFixed(2).padStart(5, "0");
      return `[${mm}:${ss}] ${l.text}`;
    })
    .join("\n");
}

function openLyricsEditor() {
  if (currentIndex === -1) return;
  lyricsEditTextarea.value = linesToText(lyricLines);
  lyricsEditOverlay.hidden = false;
  lyricsEditTextarea.focus();
}

function closeLyricsEditor() {
  lyricsEditOverlay.hidden = true;
}

editLyricsBtn.onclick = openLyricsEditor;
lyricsEditCancel.onclick = closeLyricsEditor;

lyricsEditSave.onclick = async () => {
  const track = currentTracks[currentIndex];
  if (!track) return;
  try {
    const res = await fetch("/api/lyrics/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: track.videoId, text: lyricsEditTextarea.value }),
    });
    if (!res.ok) throw new Error("Save failed");
  } catch (err) {
    alert(`Couldn't save lyrics: ${err.message}`);
    return;
  }
  closeLyricsEditor();
  resetLyrics("Loading lyrics…");
  loadLyrics(track);
};

document.addEventListener("fullscreenchange", () => {
  setTvMode(Boolean(document.fullscreenElement));
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !lyricsEditOverlay.hidden) {
    closeLyricsEditor();
    return;
  }

  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return;

  switch (event.key) {
    case " ":
      event.preventDefault();
      togglePlayPause();
      break;
    case "ArrowRight":
      playNext();
      break;
    case "ArrowLeft":
      playPrev();
      break;
    case "f":
    case "F":
      toggleTvMode();
      break;
    case "[":
      nudgeLyricOffset(-0.1);
      break;
    case "]":
      nudgeLyricOffset(0.1);
      break;
    case "0":
      nudgeLyricOffset(-currentOffsetSec);
      break;
    default:
      break;
  }
});

function onPlayerStateChange(event) {
  playBtn.textContent = event.data === YT.PlayerState.PLAYING ? "⏸" : "▶";
  if (event.data === YT.PlayerState.ENDED) {
    if (currentIndex + 1 < currentTracks.length) {
      playNext();
    } else {
      autoRadio();
    }
  }
}

async function autoRadio() {
  const lastTrack = currentTracks[currentIndex];
  if (!lastTrack || !lastTrack.videoId) return;
  try {
    const suggestions = await api(`/api/radio/${lastTrack.videoId}`);
    if (suggestions.length === 0) return;
    applyQueueState(await postJson("/api/queue/add_many", { tracks: suggestions }));
    playNext();
  } catch (err) {
    // no continuation available -- just stop, same as before
  }
}

const YT_PLAYER_ERROR_MESSAGES = {
  2: "That video's ID looks invalid.",
  5: "That video can't be played in this embedded player.",
  100: "That video was removed or is private.",
  101: "The uploader doesn't allow this video to be played in embedded players.",
  150: "The uploader doesn't allow this video to be played in embedded players.",
};

function onPlayerError(event) {
  const message = YT_PLAYER_ERROR_MESSAGES[event.data] || "This video can't be played here.";
  resetLyrics(`${message} Try skipping to another track.`);
}

window.onYouTubeIframeAPIReady = function () {
  ytPlayer = new YT.Player("yt-player", {
    height: "180",
    width: "320",
    playerVars: { autoplay: 1 },
    events: {
      onReady: () => {
        playerReady = true;
        playBtn.disabled = false;
        prevBtn.disabled = false;
        nextBtn.disabled = false;
        playBtn.title = "";
        prevBtn.title = "";
        nextBtn.title = "";
        syncTimer = setInterval(syncLyrics, 200);
        populateSpeedOptions();
        const track = currentTracks[currentIndex];
        if (track && track.videoId) {
          ytPlayer.loadVideoById(track.videoId);
        }
      },
      onStateChange: onPlayerStateChange,
      onError: onPlayerError,
    },
  });
};

// If the YouTube IFrame API never calls back (blocked script, no internet,
// DNS filtering, etc.), tell the user instead of leaving the transport
// controls silently disabled forever with no explanation.
setTimeout(() => {
  if (!playerReady) {
    resetLyrics(
      "Couldn't load YouTube's player. Check your internet connection (or any DNS/ad " +
        "blocking) and refresh the page."
    );
  }
}, 10000);

const TOUR_STEPS = [
  {
    selector: "#search-box",
    text: "Search your whole library here — or all of YouTube Music, if your library doesn't have it.",
  },
  {
    selector: "#queue-btn",
    text: "See what's queued up next, remove songs, or shuffle the order.",
  },
  {
    selector: "#tv-mode-btn",
    text: "Go fullscreen with big lyrics — perfect when this is up on a TV.",
  },
  {
    selector: "#party-info",
    text: "Guests on your WiFi can scan this to add songs from their phone.",
  },
];

let tourIndex = 0;
let tourTarget = null;

function showTourStep(index) {
  if (tourTarget) tourTarget.classList.remove("tour-highlight");

  if (index >= TOUR_STEPS.length) {
    endTour();
    return;
  }

  const step = TOUR_STEPS[index];
  const target = document.querySelector(step.selector);
  if (!target || target.hidden || target.offsetParent === null) {
    showTourStep(index + 1);
    return;
  }

  tourIndex = index;
  tourTarget = target;
  target.classList.add("tour-highlight");

  const rect = target.getBoundingClientRect();
  tourTextEl.textContent = step.text;
  tourNextBtn.textContent = index === TOUR_STEPS.length - 1 ? "Got it!" : "Next";
  tourTooltipEl.style.top = `${rect.bottom + 8}px`;
  tourTooltipEl.style.left = `${Math.max(8, rect.left)}px`;
  tourTooltipEl.hidden = false;
}

function startTour() {
  showTourStep(0);
}

function endTour() {
  if (tourTarget) tourTarget.classList.remove("tour-highlight");
  tourTarget = null;
  tourTooltipEl.hidden = true;
  try {
    localStorage.setItem("karaoke_tour_seen", "true");
  } catch (err) {
    // ignore -- storage unavailable, just won't remember for next time
  }
}

if (refreshLibraryBtn) {
  refreshLibraryBtn.onclick = async () => {
    refreshLibraryBtn.disabled = true;
    try {
      await loadSearchIndex(true);
      if (!viewingQueue) loadLibrary();
    } finally {
      refreshLibraryBtn.disabled = false;
    }
  };
}

if (hostUnlockBtn) {
  hostUnlockBtn.onclick = promptForHostPin;
}

tourNextBtn.onclick = () => showTourStep(tourIndex + 1);
tourSkipBtn.onclick = endTour;
helpBtn.onclick = startTour;

loadLibrary();
loadSearchIndex();
refreshQueue();
setInterval(refreshQueue, 3000);
loadPartyInfo();
pollReactions();
setInterval(pollReactions, 1500);

let tourSeen = false;
try {
  tourSeen = Boolean(localStorage.getItem("karaoke_tour_seen"));
} catch (err) {
  // ignore -- storage unavailable, just show the tour every time
}
if (!tourSeen) {
  // give party-info a moment to finish loading so its tour step has a target
  setTimeout(startTour, 1500);
}
