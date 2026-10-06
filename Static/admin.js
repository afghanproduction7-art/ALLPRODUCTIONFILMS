const tg = window.Telegram.WebApp;

tg.ready();
tg.expand();

const initData = tg.initData || "";

const headers = {
    "X-Telegram-Init-Data": initData,
    "Content-Type": "application/json"
};


function show(id) {
    document.getElementById(id)
        .classList.remove("hidden");
}


function hide(id) {
    document.getElementById(id)
        .classList.add("hidden");
}


function setText(id, value) {
    const element =
        document.getElementById(id);

    if (element) {
        element.textContent =
            value ?? "";
    }
}


async function api(
    url,
    options = {}
) {

    const response = await fetch(
        url,
        {
            ...options,
            headers: {
                ...headers,
                ...(options.headers || {})
            }
        }
    );

    let data = null;

    try {
        data = await response.json();
    } catch {
        data = null;
    }

    if (!response.ok) {

        const message =
            data?.detail ||
            `HTTP ${response.status}`;

        throw new Error(message);
    }

    return data;
}


/* =========================================
   INITIALIZATION
========================================= */

async function initialize() {

    if (!initData) {

        show("accessError");

        document.querySelector(
            "#accessError h2"
        ).textContent =
            "Telegram Required";

        document.querySelector(
            "#accessError p"
        ).textContent =
            "Please open this Admin Panel from Telegram.";

        return;
    }

    try {

        await loadDashboard();

        show("adminContent");

    } catch (error) {

        console.error(error);

        show("accessError");

        document.querySelector(
            "#accessError h2"
        ).textContent =
            "Access Denied";

        document.querySelector(
            "#accessError p"
        ).textContent =
            error.message;
    }
}


/* =========================================
   DASHBOARD
========================================= */

async function loadDashboard() {

    await Promise.all([
        loadStats(),
        loadSettings(),
        loadChannels(),
        loadFilms()
    ]);
}


/* =========================================
   STATISTICS
========================================= */

async function loadStats() {

    const data =
        await api(
            "/api/admin/stats"
        );

    setText(
        "usersCount",
        data.users
    );

    setText(
        "publishersCount",
        data.publishers
    );

    setText(
        "filmsCount",
        data.films
    );

    setText(
        "approvedFilmsCount",
        data.approved_films
    );
}


/* =========================================
   SETTINGS
========================================= */

async function loadSettings() {

    const data =
        await api(
            "/api/admin/settings"
        );

    document.getElementById(
        "referralTarget"
    ).value =
        data.referral_target;

    document.getElementById(
        "mainChannel"
    ).value =
        data.main_channel;

    document.getElementById(
        "accessChannel"
    ).value =
        data.access_channel;

    document.getElementById(
        "autoApprove"
    ).checked =
        Boolean(
            data.auto_approve_films
        );
}


/* =========================================
   SAVE SETTINGS
========================================= */

async function saveSettings() {

    const target =
        Number(
            document.getElementById(
                "referralTarget"
            ).value
        );

    const mainChannel =
        document.getElementById(
            "mainChannel"
        ).value.trim();

    const accessChannel =
        document.getElementById(
            "accessChannel"
        ).value.trim();

    const autoApprove =
        document.getElementById(
            "autoApprove"
        ).checked;

    if (!target || target < 1) {

        showMessage(
            "Referral Target must be at least 1.",
            true
        );

        return;
    }

    try {

        await api(
            "/api/admin/settings",
            {
                method: "POST",

                body: JSON.stringify({
                    referral_target:
                        target,

                    auto_approve_films:
                        autoApprove,

                    main_channel:
                        mainChannel,

                    access_channel:
                        accessChannel
                })
            }
        );

        showMessage(
            "Settings saved successfully."
        );

        await loadStats();

    } catch (error) {

        showMessage(
            error.message,
            true
        );
    }
}


function showMessage(
    message,
    error = false
) {

    const element =
        document.getElementById(
            "settingsMessage"
        );

    element.textContent =
        message;

    element.style.color =
        error
            ? "#ff5d6c"
            : "#35d07f";

    setTimeout(() => {

        element.textContent = "";

    }, 4000);
}


/* =========================================
   CHANNELS
========================================= */

async function loadChannels() {

    const channels =
        await api(
            "/api/admin/channels"
        );

    const container =
        document.getElementById(
            "channelsList"
        );

    if (!channels.length) {

        container.innerHTML =
            `<div class="empty">
                No channels found.
            </div>`;

        return;
    }

    container.innerHTML =
        channels.map(
            channel =>
                renderChannel(channel)
        ).join("");
}


function renderChannel(channel) {

    const status =
        channel.active
            ? "🟢 Active"
            : "🔴 Disabled";

    return `
        <div class="list-item">

            <div class="list-info">

                <div class="list-title">
                    ${escapeHtml(
                        channel.title
                    )}
                </div>

                <div class="list-meta">
                    ${escapeHtml(
                        channel.username
                    )}
                    <br>
                    ${escapeHtml(
                        channel.category
                    )}
                    · ${status}
                </div>

            </div>

            <div class="list-actions">

                <button
                    class="small-btn"
                    onclick="toggleChannel(
                        ${channel.id},
                        ${!channel.active}
                    )"
                >
                    ${channel.active
                        ? "⏸ Disable"
                        : "▶️ Enable"}
                </button>

                <button
                    class="danger-btn"
                    onclick="removeChannel(
                        ${channel.id}
                    )"
                >
                    🗑 Delete
                </button>

            </div>

        </div>
    `;
}


/* =========================================
   ADD CHANNEL
========================================= */

async function addNewChannel() {

    const title =
        document.getElementById(
            "channelTitle"
        ).value.trim();

    const username =
        document.getElementById(
            "channelUsername"
        ).value.trim();

    const category =
        document.getElementById(
            "channelCategory"
        ).value;

    if (!title || !username) {

        alert(
            "Please enter channel name and username."
        );

        return;
    }

    try {

        await api(
            "/api/admin/channels",
            {
                method: "POST",

                body: JSON.stringify({
                    title,
                    username,
                    category
                })
            }
        );

        document.getElementById(
            "channelTitle"
        ).value = "";

        document.getElementById(
            "channelUsername"
        ).value = "";

        await loadChannels();

    } catch (error) {

        alert(
            error.message
        );
    }
}


/* =========================================
   TOGGLE CHANNEL
========================================= */

async function toggleChannel(
    channelId,
    active
) {

    try {

        await api(
            `/api/admin/channels/${channelId}`,
            {
                method: "PUT",

                body: JSON.stringify({
                    active
                })
            }
        );

        await loadChannels();

    } catch (error) {

        alert(
            error.message
        );
    }
}


/* =========================================
   DELETE CHANNEL
========================================= */

async function removeChannel(
    channelId
) {

    const confirmed =
        confirm(
            "Are you sure you want to delete this channel?"
        );

    if (!confirmed) {
        return;
    }

    try {

        await api(
            `/api/admin/channels/${channelId}`,
            {
                method: "DELETE"
            }
        );

        await loadChannels();

    } catch (error) {

        alert(
            error.message
        );
    }
}


/* =========================================
   FILMS
========================================= */

async function loadFilms() {

    const films =
        await api(
            "/api/admin/films"
        );

    const container =
        document.getElementById(
            "filmsList"
        );

    if (!films.length) {

        container.innerHTML =
            `<div class="empty">
                🎬 No films found.
            </div>`;

        return;
    }

    container.innerHTML =
        films.map(
            film =>
                renderFilm(film)
        ).join("");
}


function renderFilm(film) {

    let status;

    if (film.approved) {

        status = `
            <span class="film-status status-approved">
                ✅ Approved
            </span>
        `;

    } else {

        status = `
            <span class="film-status status-rejected">
                ❌ Rejected
            </span>
        `;
    }

    const poster =
        film.poster_url
            ? film.poster_url
            : "";

    return `
        <div class="list-item">

            <div class="film-item">

                ${
                    poster
                    ? `
                        <img
                            class="film-poster"
                            src="${poster}"
                            alt=""
                            loading="lazy"
                        >
                    `
                    : `
                        <div
                            class="film-poster"
                            style="
                                display:flex;
                                align-items:center;
                                justify-content:center;
                                font-size:25px;
                            "
                        >
                            🎬
                        </div>
                    `
                }

                <div class="film-info">

                    <div class="film-title">
                        ${escapeHtml(
                            film.title
                        )}
                    </div>

                    <div class="film-meta">

                        ${
                            film.year
                            ? `📅 ${escapeHtml(
                                film.year
                            )}<br>`
                            : ""
                        }

                        ${
                            film.quality
                            ? `⚙️ ${escapeHtml(
                                film.quality
                            )}<br>`
                            : ""
                        }

                        ${
                            film.genre
                            ? `🎭 ${escapeHtml(
                                film.genre
                            )}<br>`
                            : ""
                        }

                        ${
                            film.category
                            ? `📁 ${escapeHtml(
                                film.category
                            )}<br>`
                            : ""
                        }

                        ${
                            film.official
                            ? "⭐ Official"
                            : ""
                        }

                        ${status}

                    </div>

                </div>

            </div>

            <div class="list-actions">

                ${
                    !film.approved
                    ? `
                        <button
                            class="success-btn"
                            onclick="approveFilm(
                                ${film.id}
                            )"
                        >
                            ✅ Approve
                        </button>
                    `
                    : `
                        <button
                            class="warning-btn"
                            onclick="rejectFilm(
                                ${film.id}
                            )"
                        >
                            🚫 Reject
                        </button>
                    `
                }

                <button
                    class="small-btn"
                    onclick="toggleOfficial(
                        ${film.id},
                        ${!film.official}
                    )"
                >
                    ${
                        film.official
                        ? "⭐ Unofficial"
                        : "⭐ Official"
                    }
                </button>

                <button
                    class="danger-btn"
                    onclick="removeFilm(
                        ${film.id}
                    )"
                >
                    🗑 Delete
                </button>

            </div>

        </div>
    `;
}


/* =========================================
   APPROVE FILM
========================================= */

async function approveFilm(
    filmId
) {

    try {

        await api(
            `/api/admin/films/${filmId}/approve`,
            {
                method: "POST"
            }
        );

        await refreshFilms();

    } catch (error) {

        alert(
            error.message
        );
    }
}


/* =========================================
   REJECT FILM
========================================= */

async function rejectFilm(
    filmId
) {

    try {

        await api(
            `/api/admin/films/${filmId}/reject`,
            {
                method: "POST"
            }
        );

        await refreshFilms();

    } catch (error) {

        alert(
            error.message
        );
    }
}


/* =========================================
   OFFICIAL FILM
========================================= */

async function toggleOfficial(
    filmId,
    official
) {

    try {

        await api(
            `/api/admin/films/${filmId}/official`,
            {
                method: "POST",

                body: JSON.stringify({
                    official
                })
            }
        );

        await refreshFilms();

    } catch (error) {

        alert(
            error.message
        );
    }
}


/* =========================================
   DELETE FILM
========================================= */

async function removeFilm(
    filmId
) {

    const confirmed =
        confirm(
            "Are you sure you want to delete this film?"
        );

    if (!confirmed) {
        return;
    }

    try {

        await api(
            `/api/admin/films/${filmId}`,
            {
                method: "DELETE"
            }
        );

        await refreshFilms();

        await loadStats();

    } catch (error) {

        alert(
            error.message
        );
    }
}


/* =========================================
   REFRESH FILMS
========================================= */

async function refreshFilms() {

    await loadFilms();

}


/* =========================================
   REFRESH EVERYTHING
========================================= */

async function refreshAll() {

    try {

        await loadDashboard();

    } catch (error) {

        alert(
            error.message
        );
    }
}


/* =========================================
   ESCAPE HTML
========================================= */

function escapeHtml(
    value
) {

    if (
        value === null ||
        value === undefined
    ) {

        return "";
    }

    return String(value)
        .replace(
            /&/g,
            "&amp;"
        )
        .replace(
            /</g,
            "&lt;"
        )
        .replace(
            />/g,
            "&gt;"
        )
        .replace(
            /"/g,
            "&quot;"
        )
        .replace(
            /'/g,
            "&#039;"
        );
}


/* =========================================
   BUTTON EVENTS
========================================= */

document
    .getElementById(
        "saveSettingsBtn"
    )
    .addEventListener(
        "click",
        saveSettings
    );


document
    .getElementById(
        "addChannelBtn"
    )
    .addEventListener(
        "click",
        addNewChannel
    );


document
    .getElementById(
        "refreshBtn"
    )
    .addEventListener(
        "click",
        refreshAll
    );


document
    .getElementById(
        "loadFilmsBtn"
    )
    .addEventListener(
        "click",
        refreshFilms
    );


/* =========================================
   START
========================================= */

initialize();
