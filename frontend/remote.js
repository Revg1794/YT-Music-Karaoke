const libraryListEl = document.getElementById("library-list");
const searchBoxEl = document.getElementById("search-box");
const queueBtn = document.getElementById("queue-btn");
const nowPlayingTextEl = document.getElementById("now-playing-text");
const guestNameEl = document.getElementById("guest-name");

let browsingTracks = [];
let queueTracks = [];
let queueIndex = -1;
let allTracksIndex = [];
let viewingQueue = false;
let rotationEnabled = true;

guestNameEl.value = localStorage.getItem("karaoke_guest_name") || "";
guestNameEl.oninput = () => {
  localStorage.setItem("karaoke_guest_name", guestNameEl.value);
};

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${path}`);
  }
  return res.json();
}

function postJson(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function applyQueueState(state) {
  queueTracks = state.tracks;
  queueIndex = state.currentIndex;
  rotationEnabled = state.rotation !== false;
  queueBtn.textContent = `🎵 Queue (${queueTracks.length})`;
  const playing = queueTracks[queueIndex];
  nowPlayingTextEl.textContent = playing ? `${playing.title} — ${playing.artist}` : "—";
  if (viewingQueue) renderQueueView();
}

async function refreshQueue() {
  try {
    applyQueueState(await api("/api/queue"));
  } catch (err) {
    // ignore transient poll failures
  }
}

async function loadSearchIndex() {
  try {
    allTracksIndex = await api("/api/library/all");
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
  renderTrackList(libraryMatches, 0);

  if (libraryMatches.length < 5) {
    const catalogHeading = document.createElement("h1");
    catalogHeading.style.marginTop = "16px";
    catalogHeading.textContent = "Searching YouTube Music…";
    libraryListEl.appendChild(catalogHeading);

    try {
      const catalogMatches = await api(`/api/search/catalog?q=${encodeURIComponent(q)}`);
      browsingTracks = [...libraryMatches, ...catalogMatches];
      catalogHeading.textContent = `From YouTube Music (${catalogMatches.length})`;
      renderTrackList(catalogMatches, libraryMatches.length);
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

  const hint = document.createElement("div");
  hint.style.cssText = "color:#6f6f7c;font-size:12px;margin-bottom:10px;";
  hint.textContent = "Tap a song to add it to the queue.";
  libraryListEl.appendChild(hint);

  renderTrackList(tracks, 0);
}

function renderTrackList(tracks, startIndex) {
  tracks.forEach((track, i) => {
    const idx = startIndex + i;
    const item = document.createElement("div");
    item.className = "track-item";
    const main = document.createElement("div");
    main.className = "t-main";
    const t = document.createElement("span");
    t.textContent = track.title;
    const a = document.createElement("span");
    a.className = "t-artist";
    a.textContent = track.artist;
    main.appendChild(t);
    main.appendChild(a);
    main.onclick = () => addToQueue(idx);
    item.appendChild(main);
    libraryListEl.appendChild(item);
  });
}

async function addToQueue(idx) {
  const track = { ...browsingTracks[idx] };
  const name = guestNameEl.value.trim();
  if (name) track.addedBy = name;
  applyQueueState(await postJson("/api/queue/add", { track }));
}

function renderQueueView() {
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
  title.textContent = `Queue (${queueTracks.length})`;
  libraryListEl.appendChild(title);

  const hint = document.createElement("div");
  hint.style.cssText = "color:#6f6f7c;font-size:12px;margin-bottom:10px;";
  hint.textContent = rotationEnabled
    ? "The host runs playback. Songs are ordered so everyone gets a turn."
    : "The host runs playback.";
  libraryListEl.appendChild(hint);

  queueTracks.forEach((track, idx) => {
    const item = document.createElement("div");
    item.className = "track-item";
    if (idx === queueIndex) item.classList.add("playing");
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

    libraryListEl.appendChild(item);
  });
}

function showQueue() {
  viewingQueue = true;
  renderQueueView();
}

document.querySelectorAll("#reactions button").forEach((btn) => {
  btn.onclick = () =>
    postJson("/api/react", {
      emoji: btn.dataset.emoji,
      name: guestNameEl.value.trim() || null,
    });
});

searchBoxEl.oninput = () => runSearch(searchBoxEl.value);
queueBtn.onclick = showQueue;

loadLibrary();
loadSearchIndex();
refreshQueue();
setInterval(refreshQueue, 3000);
