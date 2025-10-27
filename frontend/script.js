const BASE_URL = window.location.origin + "/api";
const queueList = document.getElementById("queue-list");
const addBtn = document.getElementById("add-btn");
const input = document.getElementById("song-link");

function getClientId() {
  let cid = localStorage.getItem("client_id");
  if (!cid) {
    try {
      cid = crypto.randomUUID();
    } catch {
      cid = Date.now().toString(36) + Math.random().toString(36).slice(2);
    }
    localStorage.setItem("client_id", cid);
  }
  return cid;
}

async function fetchQueue() {
  try {
    const res = await fetch(`${BASE_URL}/queue`, {
      headers: { "X-Client-Id": getClientId() }
    });
    if (!res.ok) throw new Error("Request failed");
    const data = await res.json();
    renderQueue(data);
  } catch (err) {
    console.error("Error fetching queue:", err);
    queueList.innerHTML = `<p class="text-center text-gray-500">Fehler beim Laden der Queue 😕</p>`;
  }
}

function renderQueue(songs) {
  if (!songs || songs.length === 0) {
    queueList.innerHTML = `
      <div class="text-center text-gray-500 italic py-4">
        🎧 Aktuell keine Songs in der Queue.<br>
        Füge direkt einen hinzu!
      </div>`;
    return;
  }

  queueList.innerHTML = songs.map(song => {
    // Markierung basierend auf client_vote
    const activeVote = song.client_vote;

    const redActive   = activeVote === -1 ? "ring-2 ring-red-400 bg-red-200" : "";
    const grayActive  = activeVote === 0  ? "ring-2 ring-gray-400 bg-gray-200" : "";
    const greenActive = activeVote === 1  ? "ring-2 ring-green-400 bg-green-200" : "";

    return `
      <div class="flex flex-col sm:flex-row sm:items-center justify-between bg-gray-50 border rounded-lg p-3 shadow-sm">
        <div class="flex flex-col sm:flex-row sm:items-center gap-2">
          <div>
            <p class="font-semibold text-gray-800">${song.name}</p>
            <p class="text-gray-500 text-sm">${song.artist}</p>
          </div>
        </div>
        <div class="flex items-center gap-2 mt-2 sm:mt-0">
          <button class="vote-btn bg-red-100 hover:bg-red-200 text-red-600 font-bold px-2 py-1 rounded ${redActive}"
                  data-vote="-1" data-id="${song.id}">😞</button>
          <button class="vote-btn bg-gray-100 hover:bg-gray-200 text-gray-700 font-bold px-2 py-1 rounded ${grayActive}"
                  data-vote="0" data-id="${song.id}">😐</button>
          <button class="vote-btn bg-green-100 hover:bg-green-200 text-green-600 font-bold px-2 py-1 rounded ${greenActive}"
                  data-vote="1" data-id="${song.id}">😄</button>
          <span class="ml-3 font-semibold text-gray-800">${song.vote_sum}</span>
        </div>
      </div>
    `;
  }).join("");

  document.querySelectorAll(".vote-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const songId = btn.dataset.id;
      const vote = parseInt(btn.dataset.vote);
      await sendVote(songId, vote);
      await fetchQueue();
    });
  });
}

async function sendVote(songId, vote) {
  try {
    const res = await fetch(`${BASE_URL}/vote`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Client-Id": getClientId()
      },
      body: JSON.stringify({ song_id: songId, vote })
    });
    if (!res.ok) throw new Error("Vote failed");
  } catch (err) {
    console.error("Vote error:", err);
    alert("Fehler beim Abstimmen.");
  }
}

addBtn.addEventListener("click", async () => {
  const link = input.value.trim();
  if (!link) return;
  try {
    const res = await fetch(`${BASE_URL}/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ song_link: link })
    });
    if (!res.ok) throw new Error(await res.text());
    input.value = "";
    await fetchQueue();
  } catch (err) {
    console.error("Add song error:", err);
    alert("Song konnte nicht hinzugefügt werden.");
  }
});

// Initial load + Auto-refresh
fetchQueue();
setInterval(fetchQueue, 10000);
