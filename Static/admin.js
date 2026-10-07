const tg = window.Telegram
    ? window.Telegram.WebApp
    : null;


let currentSection = "dashboard";


/* ---------------------------------------------------------
   Telegram
--------------------------------------------------------- */

if (tg) {
    tg.ready();
    tg.expand();
}


/* ---------------------------------------------------------
   API
--------------------------------------------------------- */

function getInitData() {
    if (!tg) {
        return "";
    }

    return tg.initData || "";
}


async function api(
    url,
    options = {}
) {
    const headers = {
        ...(options.headers || {}),
        "X-Telegram-Init-Data": getInitData(),
    };

    if (
        options.body &&
        !headers["Content-Type"]
    ) {
        headers["Content-Type"] =
            "application/json";
    }

    const response = await fetch(
        url,
        {
            ...options,
            headers,
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
            data?.message ||
            "Request failed";

        throw new Error(message);
    }

    return data;
}


/* ---------------------------------------------------------
   Helpers
--------------------------------------------------------- */

function escapeHtml(value) {
    if (
        value === null ||
        value === undefined
    ) {
        return "";
    }

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function showToast(
    message,
    type = "success"
) {
    const toast =
        document.getElementById("toast");

    if (!toast) {
        return;
    }

    toast.textContent = message;
    toast.className =
        `toast show ${type}`;

    clearTimeout(
        window.__toastTimer
    );

    window.__toastTimer =
        setTimeout(() => {
            toast.className = "toast";
        }, 3000);
}


function openModal(id) {
    const modal =
        document.getElementById(id);

    if (modal) {
        modal.classList.remove("hidden");
    }
}


function closeModal(id) {
    const modal =
        document.getElementById(id);

    if (modal) {
        modal.classList.add("hidden");
    }
}


/* ---------------------------------------------------------
   Navigation
--------------------------------------------------------- */

function switchSection(section) {

    currentSection = section;

    document
        .querySelectorAll(".admin-section")
        .forEach(item => {
            item.classList.remove("active");
        });

    const target =
        document.getElementById(
            `${section}Section`
        );

    if (target) {
        target.classList.add("active");
    }


    document
        .querySelectorAll(".nav-btn")
        .forEach(button => {
            button.classList.toggle(
                "active",
                button.dataset.section === section
            );
        });


    const titles = {
        dashboard: [
            "Dashboard",
            "Manage your movie platform",
        ],

        films: [
            "Films",
            "Manage submitted and approved films",
        ],

        users: [
            "Users",
            "View users and referral progress",
        ],

        channels: [
            "Channels",
            "Manage platform channels",
        ],

        settings: [
            "Settings",
            "Configure platform settings",
        ],
    };


    const title =
        titles[section] || titles.dashboard;

    document.getElementById(
        "pageTitle"
    ).textContent = title[0];

    document.getElementById(
        "pageSubtitle"
    ).textContent = title[1];


    if (section === "dashboard") {
        loadDashboard();
    }

    if (section === "films") {
        loadPendingFilms();
    }

    if (section === "users") {
        loadUsers();
    }

    if (section === "channels") {
        loadChannels();
    }

    if (section === "settings") {
        loadSettings();
    }
}


/* ---------------------------------------------------------
   Dashboard
--------------------------------------------------------- */

async function loadDashboard() {

    try {

        const stats =
            await api(
                "/api/admin/stats"
            );

        document.getElementById(
            "statUsers"
        ).textContent =
            stats.users ?? 0;

        document.getElementById(
            "statPublishers"
        ).textContent =
            stats.publishers ?? 0;

        document.getElementById(
            "statFilms"
        ).textContent =
            stats.films ?? 0;

        document.getElementById(
            "statApprovedFilms"
        ).textContent =
            stats.approved_films ?? 0;

        document.getElementById(
            "statChannels"
        ).textContent =
            stats.channels ?? 0;


        const settings =
            await api(
                "/api/admin/settings"
            );

        document.getElementById(
            "overviewReferralTarget"
        ).textContent =
            settings.referral_target ?? 0;

        document.getElementById(
            "overviewMainChannel"
        ).textContent =
            settings.main_channel || "—";

        document.getElementById(
            "overviewAccessChannel"
        ).textContent =
            settings.access_channel || "—";

        document.getElementById(
            "overviewAutoApprove"
        ).textContent =
            settings.auto_approve_films
                ? "ON"
                : "OFF";

    } catch (error) {

        showToast(
            error.message,
            "error"
        );
    }
}


/* ---------------------------------------------------------
   Films
--------------------------------------------------------- */

async function loadPendingFilms() {

    const container =
        document.getElementById(
            "filmsContainer"
        );

    container.innerHTML =
        `<div class="loading">
            Loading films...
        </div>`;

    try {

        const films =
            await api(
                "/api/admin/films/pending"
            );

        if (!films.length) {

            container.innerHTML =
                `<div class="empty-state">
                    🎬 No pending films found.
                </div>`;

            return;
        }


        container.innerHTML =
            films.map(
                filmCard
            ).join("");

    } catch (error) {

        container.innerHTML =
            `<div class="empty-state">
                ❌ ${escapeHtml(
                    error.message
                )}
            </div>`;
    }
}


function filmCard(film) {

    const poster =
        film.poster_url
            ? `<img
                class="film-poster"
                src="${escapeHtml(
                    film.poster_url
                )}"
                alt="${escapeHtml(
                    film.title
                )}"
                loading="lazy"
            >`
            : `<div
                class="film-poster"
                style="
                    display:grid;
                    place-items:center;
                    color:#8d99ad;
                "
            >
                🎬 No Poster
            </div>`;


    const status =
        film.approved
            ? `<span class="status approved">
                Approved
              </span>`
            : `<span class="status pending">
                Pending
              </span>`;


    return `
        <article class="film-card">

            ${poster}

            <div class="film-card-body">

                <h3>
                    ${escapeHtml(
                        film.title
                    )}
                </h3>

                <div class="film-meta">
                    📅 ${escapeHtml(
                        film.year || "—"
                    )}
                    <br>

                    ⚙️ ${escapeHtml(
                        film.quality || "—"
                    )}
                    <br>

                    🎭 ${escapeHtml(
                        film.genre || "—"
                    )}
                    <br>

                    🔊 ${escapeHtml(
                        film.language || "—"
                    )}
                    <br>

                    ${status}
                </div>


                <div class="film-actions">

                    <button
                        class="primary-btn"
                        onclick="viewFilm(
                            ${film.id}
                        )"
                    >
                        👁 View
                    </button>

                    ${
                        film.approved
                            ? `
                                <button
                                    class="danger-btn"
                                    onclick="setFilmApproval(
                                        ${film.id},
                                        false
                                    )"
                                >
                                    Reject
                                </button>
                              `
                            : `
                                <button
                                    class="primary-btn"
                                    onclick="setFilmApproval(
                                        ${film.id},
                                        true
                                    )"
                                >
                                    ✅ Approve
                                </button>
                              `
                    }

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

        </article>
    `;
}


async function viewFilm(filmId) {

    try {

        const film =
            await api(
                `/api/films/${filmId}`
            );

        const poster =
            film.poster_url
                ? `
                    <img
                        class="modal-film-poster"
                        src="${escapeHtml(
                            film.poster_url
                        )}"
                        alt="${escapeHtml(
                            film.title
                        )}"
                    >
                  `
                : "";


        document.getElementById(
            "filmModalContent"
        ).innerHTML = `

            ${poster}

            <h2>
                ${escapeHtml(
                    film.title
                )}
            </h2>

            <div class="film-detail-grid">

                <div class="film-detail-item">
                    <span>Year</span>
                    ${escapeHtml(
                        film.year || "—"
                    )}
                </div>

                <div class="film-detail-item">
                    <span>Quality</span>
                    ${escapeHtml(
                        film.quality || "—"
                    )}
                </div>

                <div class="film-detail-item">
                    <span>Genre</span>
                    ${escapeHtml(
                        film.genre || "—"
                    )}
                </div>

                <div class="film-detail-item">
                    <span>Language</span>
                    ${escapeHtml(
                        film.language || "—"
                    )}
                </div>

                <div class="film-detail-item">
                    <span>Category</span>
                    ${escapeHtml(
                        film.category || "—"
                    )}
                </div>

                <div class="film-detail-item">
                    <span>Status</span>
                    ${
                        film.approved
                            ? "Approved"
                            : "Pending"
                    }
                </div>

            </div>

            <div class="film-description">
                ${
                    escapeHtml(
                        film.description ||
                        "No description"
                    )
                }
            </div>

            <div class="film-actions">

                ${
                    film.approved
                        ? `
                            <button
                                class="danger-btn"
                                onclick="setFilmApproval(
                                    ${film.id},
                                    false
                                )"
                            >
                                Reject
                            </button>
                          `
                        : `
                            <button
                                class="primary-btn"
                                onclick="setFilmApproval(
                                    ${film.id},
                                    true
                                )"
                            >
                                ✅ Approve
                            </button>
                          `
                }

                <button
                    class="danger-btn"
                    onclick="removeFilm(
                        ${film.id}
                    )"
                >
                    🗑 Delete
                </button>

            </div>
        `;

        openModal("filmModal");

    } catch (error) {

        showToast(
            error.message,
            "error"
        );
    }
}


async function setFilmApproval(
    filmId,
    approved
) {

    const action =
        approved
            ? "approve"
            : "reject";

    const confirmed =
        confirm(
            `Are you sure you want to ${action} this film?`
        );

    if (!confirmed) {
        return;
    }


    try {

        await api(
            `/api/admin/films/${filmId}`,
            {
                method: "PUT",
                body: JSON.stringify({
                    approved: approved,
                }),
            }
        );

        showToast(
            approved
                ? "Film approved successfully"
                : "Film rejected"
        );

        closeModal("filmModal");

        await loadPendingFilms();

        await loadDashboard();

    } catch (error) {

        showToast(
            error.message,
            "error"
        );
    }
}


async function removeFilm(filmId) {

    const confirmed =
        confirm(
            "Delete this film permanently?"
        );

    if (!confirmed) {
        return;
    }


    try {

        await api(
            `/api/admin/films/${filmId}`,
            {
                method: "DELETE",
            }
        );

        showToast(
            "Film deleted successfully"
        );

        closeModal("filmModal");

        await loadPendingFilms();

        await loadDashboard();

    } catch (error) {

        showToast(
            error.message,
            "error"
        );
    }
}


/* ---------------------------------------------------------
   Users
--------------------------------------------------------- */

async function loadUsers() {

    const body =
        document.getElementById(
            "usersTableBody"
        );

    body.innerHTML =
        `<tr>
            <td colspan="7">
                Loading users...
            </td>
        </tr>`;


    try {

        const users =
            await api(
                "/api/admin/users?limit=500"
            );


        if (!users.length) {

            body.innerHTML =
                `<tr>
                    <td colspan="7">
                        No users found.
                    </td>
                </tr>`;

            return;
        }


        body.innerHTML =
            users.map(
                user => `
                    <tr>

                        <td>
                            ${escapeHtml(
                                user.id
                            )}
                        </td>

                        <td>

                            <div class="user-name">
                                ${escapeHtml(
                                    user.first_name ||
                                    "Unknown"
                                )}
                            </div>

                            ${
                                user.username
                                    ? `
                                        <div
                                            class="user-username"
                                        >
                                            @${escapeHtml(
                                                user.username
                                            )}
                                        </div>
                                      `
                                    : ""
                            }

                        </td>

                        <td>
                            ${escapeHtml(
                                user.telegram_id
                            )}
                        </td>

                        <td>
                            ${escapeHtml(
                                user.referral_count
                            )}
                        </td>

                        <td>
                            ${
                                user.can_publish
                                    ? "✅ Yes"
                                    : "❌ No"
                            }
                        </td>

                        <td>
                            ${
                                user.is_blocked
                                    ? "🚫 Yes"
                                    : "✅ No"
                            }
                        </td>

                        <td>
                            ${
                                user.created_at
                                    ? new Date(
                                        user.created_at
                                      ).toLocaleDateString()
                                    : "—"
                            }
                        </td>

                    </tr>
                `
            ).join("");

    } catch (error) {

        body.innerHTML =
            `<tr>
                <td colspan="7">
                    ❌ ${escapeHtml(
                        error.message
                    )}
                </td>
            </tr>`;
    }
}


/* ---------------------------------------------------------
   Channels
--------------------------------------------------------- */

async function loadChannels() {

    const container =
        document.getElementById(
            "channelsContainer"
        );

    container.innerHTML =
        `<div class="loading">
            Loading channels...
        </div>`;


    try {

        const channels =
            await api(
                "/api/admin/channels"
            );


        if (!channels.length) {

            container.innerHTML =
                `<div class="empty-state">
                    📢 No channels configured.
                </div>`;

            return;
        }


        container.innerHTML =
            channels.map(
                channel => `
                    <article
                        class="channel-card"
                    >

                        <h3>
                            ${escapeHtml(
                                channel.title
                            )}
                        </h3>

                        <div
                            class="channel-username"
                        >
                            ${escapeHtml(
                                channel.username
                            )}
                        </div>

                        <div
                            class="channel-category"
                        >
                            Category:
                            ${escapeHtml(
                                channel.category
                            )}
                            <br>
                            Status:
                            ${
                                channel.active
                                    ? "✅ Active"
                                    : "❌ Inactive"
                            }
                        </div>


                        <div
                            class="channel-actions"
                        >

                            <button
                                class="primary-btn"
                                onclick="editChannel(
                                    ${channel.id},
                                    '${escapeJs(
                                        channel.title
                                    )}',
                                    '${escapeJs(
                                        channel.username
                                    )}',
                                    '${escapeJs(
                                        channel.category
                                    )}',
                                    ${channel.active}
                                )"
                            >
                                ✏️ Edit
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

                    </article>
                `
            ).join("");

    } catch (error) {

        container.innerHTML =
            `<div class="empty-state">
                ❌ ${escapeHtml(
                    error.message
                )}
            </div>`;
    }
}


function escapeJs(value) {
    return String(
        value ?? ""
    )
        .replaceAll("\\", "\\\\")
        .replaceAll("'", "\\'");
}


function openAddChannelModal() {

    document.getElementById(
        "channelModalTitle"
    ).textContent =
        "Add Channel";

    document.getElementById(
        "channelId"
    ).value = "";

    document.getElementById(
        "channelTitle"
    ).value = "";

    document.getElementById(
        "channelUsername"
    ).value = "";

    document.getElementById(
        "channelCategory"
    ).value = "pashto";

    document.getElementById(
        "channelActive"
    ).checked = true;

    document.getElementById(
        "channelActiveRow"
    ).classList.add("hidden");

    openModal("channelModal");
}


function editChannel(
    id,
    title,
    username,
    category,
    active
) {

    document.getElementById(
        "channelModalTitle"
    ).textContent =
        "Edit Channel";

    document.getElementById(
        "channelId"
    ).value = id;

    document.getElementById(
        "channelTitle"
    ).value = title;

    document.getElementById(
        "channelUsername"
    ).value = username;

    document.getElementById(
        "channelCategory"
    ).value = category;

    document.getElementById(
        "channelActive"
    ).checked = active;

    document.getElementById(
        "channelActiveRow"
    ).classList.remove("hidden");

    openModal("channelModal");
}


async function saveChannel(event) {

    event.preventDefault();


    const id =
        document.getElementById(
            "channelId"
        ).value.trim();

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
        ).value.trim() ||
        "pashto";


    try {

        if (!id) {

            await api(
                "/api/admin/channels",
                {
                    method: "POST",
                    body: JSON.stringify({
                        title,
                        username,
                        category,
                    }),
                }
            );

            showToast(
                "Channel added successfully"
            );

        } else {

            await api(
                `/api/admin/channels/${id}`,
                {
                    method: "PUT",
                    body: JSON.stringify({
                        title,
                        username,
                        category,
                        active:
                            document.getElementById(
                                "channelActive"
                            ).checked,
                    }),
                }
            );

            showToast(
                "Channel updated successfully"
            );
        }


        closeModal("channelModal");

        await loadChannels();

        await loadDashboard();

    } catch (error) {

        showToast(
            error.message,
            "error"
        );
    }
}


async function removeChannel(id) {

    const confirmed =
        confirm(
            "Delete this channel?"
        );

    if (!confirmed) {
        return;
    }


    try {

        await api(
            `/api/admin/channels/${id}`,
            {
                method: "DELETE",
            }
        );

        showToast(
            "Channel deleted successfully"
        );

        await loadChannels();

        await loadDashboard();

    } catch (error) {

        showToast(
            error.message,
            "error"
        );
    }
}


/* ---------------------------------------------------------
   Settings
--------------------------------------------------------- */

async function loadSettings() {

    try {

        const settings =
            await api(
                "/api/admin/settings"
            );


        document.getElementById(
            "settingReferralTarget"
        ).value =
            settings.referral_target ?? 50;


        document.getElementById(
            "settingMainChannel"
        ).value =
            settings.main_channel || "";


        document.getElementById(
            "settingAccessChannel"
        ).value =
            settings.access_channel || "";


        document.getElementById(
            "settingAutoApprove"
        ).checked =
            Boolean(
                settings.auto_approve_films
            );

    } catch (error) {

        showToast(
            error.message,
            "error"
        );
    }
}


async function saveSettings() {

    const referralTarget =
        document.getElementById(
            "settingReferralTarget"
        ).value;


    const mainChannel =
        document.getElementById(
            "settingMainChannel"
        ).value.trim();


    const accessChannel =
        document.getElementById(
            "settingAccessChannel"
        ).value.trim();


    const autoApprove =
        document.getElementById(
            "settingAutoApprove"
        ).checked;


    try {

        await api(
            "/api/admin/settings",
            {
                method: "PUT",

                body: JSON.stringify({
                    referral_target:
                        Number(
                            referralTarget
                        ),

                    main_channel:
                        mainChannel,

                    access_channel:
                        accessChannel,

                    auto_approve_films:
                        autoApprove,
                }),
            }
        );


        showToast(
            "Settings saved successfully"
        );

        await loadDashboard();

    } catch (error) {

        showToast(
            error.message,
            "error"
        );
    }
}


/* ---------------------------------------------------------
   Events
--------------------------------------------------------- */

document.addEventListener(
    "DOMContentLoaded",
    () => {

        document
            .querySelectorAll(".nav-btn")
            .forEach(button => {

                button.addEventListener(
                    "click",
                    () => {
                        switchSection(
                            button.dataset.section
                        );
                    }
                );

            });


        document.getElementById(
            "refreshBtn"
        )?.addEventListener(
            "click",
            () => {
                switchSection(
                    currentSection
                );
            }
        );


        document.getElementById(
            "loadPendingBtn"
        )?.addEventListener(
            "click",
            loadPendingFilms
        );


        document.getElementById(
            "loadUsersBtn"
        )?.addEventListener(
            "click",
            loadUsers
        );


        document.getElementById(
            "addChannelBtn"
        )?.addEventListener(
            "click",
            openAddChannelModal
        );


        document.getElementById(
            "channelForm"
        )?.addEventListener(
            "submit",
            saveChannel
        );


        document.getElementById(
            "saveSettingsBtn"
        )?.addEventListener(
            "click",
            saveSettings
        );


        document
            .querySelectorAll(
                "[data-close]"
            )
            .forEach(button => {

                button.addEventListener(
                    "click",
                    () => {
                        closeModal(
                            button.dataset.close
                        );
                    }
                );

            });


        document
            .querySelectorAll(".modal")
            .forEach(modal => {

                modal.addEventListener(
                    "click",
                    event => {

                        if (
                            event.target ===
                            modal
                        ) {
                            modal.classList.add(
                                "hidden"
                            );
                        }

                    }
                );

            });


        loadDashboard();

    }
);


/* ---------------------------------------------------------
   Global functions
--------------------------------------------------------- */

window.switchSection =
    switchSection;

window.viewFilm =
    viewFilm;

window.setFilmApproval =
    setFilmApproval;

window.removeFilm =
    removeFilm;

window.editChannel =
    editChannel;

window.removeChannel =
    removeChannel;
