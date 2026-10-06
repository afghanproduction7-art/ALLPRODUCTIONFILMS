const tg = window.Telegram.WebApp;

tg.ready();
tg.expand();

const initData = tg.initData || "";

const headers = {
    "X-Telegram-Init-Data": initData,
    "Content-Type": "application/json"
};


// =========================================================
// API
// =========================================================

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

        const error =
            new Error(
                data?.detail ||
                `HTTP ${response.status}`
            );

        error.status =
            response.status;

        error.detail =
            data?.detail;

        throw error;
    }

    return data;
}


// =========================================================
// HELPERS
// =========================================================

function escapeHtml(value) {

    if (
        value === null ||
        value === undefined
    ) {
        return "";
    }

    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


function $(id) {
    return document.getElementById(id);
}


function show(id) {

    const element = $(id);

    if (element) {
        element.classList.remove(
            "hidden"
        );
    }
}


function hide(id) {

    const element = $(id);

    if (element) {
        element.classList.add(
            "hidden"
        );
    }
}


// =========================================================
// ACCESS REQUIRED
// =========================================================

function showAccessRequired() {

    let box =
        $("accessRequired");

    if (!box) {

        box =
            document.createElement(
                "div"
            );

        box.id =
            "accessRequired";

        box.innerHTML = `
            <div class="access-box">

                <div class="access-icon">
                    🔒
                </div>

                <h2>
                    لومړی چینل Join کړئ
                </h2>

                <p>
                    د فلمونو د لټون او ترلاسه کولو
                    لپاره باید لومړی زموږ لازمي
                    پښتو چینل Join کړئ.
                </p>

                <a
                    href="https://t.me/ALL_PASHTO"
                    target="_blank"
                    class="access-button"
                >
                    📢 چینل Join کړئ
                </a>

                <button
                    id="checkAccessBtn"
                    class="access-check"
                >
                    ✅ ما Join کړ، بیا یې وګوره
                </button>

            </div>
        `;

        document.body.appendChild(
            box
        );

        const checkButton =
            $("checkAccessBtn");

        if (checkButton) {

            checkButton.onclick =
                initialize;
        }
    }

    box.classList.remove(
        "hidden"
    );
}


function hideAccessRequired() {

    const box =
        $("accessRequired");

    if (box) {

        box.classList.add(
            "hidden"
        );
    }
}


// =========================================================
// USER
// =========================================================

let currentUser = null;

async function loadMe() {

    const data =
        await api(
            "/api/me"
        );

    currentUser =
        data;

    return data;
}


// =========================================================
// REFERRALS
// =========================================================

async function loadReferralInfo() {

    try {

        const data =
            await api(
                "/api/referrals"
            );

        updateReferralUI(
            data
        );

        return data;

    } catch (error) {

        console.error(
            "Referral error:",
            error
        );

        return null;
    }
}


function updateReferralUI(
    data
) {

    const count =
        data.count || 0;

    const target =
        data.target || 50;

    const remaining =
        Math.max(
            target - count,
            0
        );

    const percentage =
        Math.min(
            (count / target) * 100,
            100
        );


    setIfExists(
        "referralCount",
        count
    );

    setIfExists(
        "referralTarget",
        target
    );

    setIfExists(
        "referralRemaining",
        remaining
    );


    const progress =
        $("referralProgress");

    if (progress) {

        progress.style.width =
            `${percentage}%`;
    }


    const referralLink =
        $("referralLink");

    if (
        referralLink &&
        data.referral_link
    ) {

        referralLink.value =
            data.referral_link;
    }


    const publishButton =
        $("publishButton");

    if (publishButton) {

        if (data.can_publish) {

            publishButton.disabled =
                false;

            publishButton.innerHTML =
                "🎬 فلم نشر کړئ";

        } else {

            publishButton.disabled =
                true;

            publishButton.innerHTML =
                `🔒 ${remaining} Referral پاتې`;
        }
    }
}


// =========================================================
// LEADERS
// =========================================================

async function loadLeaders() {

    try {

        const leaders =
            await api(
                "/api/referrals/leaders"
            );

        renderLeaders(
            leaders
        );

    } catch (error) {

        console.error(
            error
        );
    }
}


function renderLeaders(
    leaders
) {

    const container =
        $("leadersList");

    if (!container) {
        return;
    }

    if (!leaders.length) {

        container.innerHTML =
            `<div class="empty">
                تر اوسه Referral نشته.
            </div>`;

        return;
    }

    container.innerHTML =
        leaders.map(
            (user, index) => {

                const name =
                    user.username
                        ? `@${escapeHtml(
                            user.username
                        )}`
                        : escapeHtml(
                            user.first_name ||
                            "User"
                        );

                return `
                    <div class="leader-item">

                        <div class="leader-rank">
                            ${
                                index === 0
                                    ? "🥇"
                                    : index === 1
                                    ? "🥈"
                                    : index === 2
                                    ? "🥉"
                                    : `#${index + 1}`
                            }
                        </div>

                        <div class="leader-name">
                            ${name}
                        </div>

                        <div class="leader-count">
                            ${user.referral_count}
                        </div>

                    </div>
                `;
            }
        ).join("");
}


// =========================================================
// FILMS
// =========================================================

async function loadLatestFilms() {

    try {

        const films =
            await api(
                "/api/films/latest"
            );

        renderFilms(
            films,
            "latestFilms"
        );

    } catch (error) {

        handleApiError(
            error
        );
    }
}


async function searchFilms(
    query
) {

    if (!query.trim()) {

        await loadLatestFilms();

        return;
    }

    try {

        const films =
            await api(
                `/api/films/search?q=${encodeURIComponent(
                    query
                )}`
            );

        renderFilms(
            films,
            "searchResults"
        );

    } catch (error) {

        handleApiError(
            error
        );
    }
}


async function loadCategory(
    category
) {

    try {

        const films =
            await api(
                `/api/films/category/${encodeURIComponent(
                    category
                )}`
            );

        renderFilms(
            films,
            "categoryResults"
        );

    } catch (error) {

        handleApiError(
            error
        );
    }
}


async function loadOfficialFilms() {

    try {

        const films =
            await api(
                "/api/films/official"
            );

        renderFilms(
            films,
            "officialFilms"
        );

    } catch (error) {

        handleApiError(
            error
        );
    }
}


// =========================================================
// FILM RENDER
// =========================================================

function renderFilms(
    films,
    containerId
) {

    const container =
        $(containerId);

    if (!container) {
        return;
    }

    if (!films.length) {

        container.innerHTML =
            `<div class="empty">
                🎬 فلم ونه موندل شو.
            </div>`;

        return;
    }

    container.innerHTML =
        films.map(
            film =>
                renderFilmCard(
                    film
                )
        ).join("");
}


function renderFilmCard(
    film
) {

    const poster =
        film.poster_url
            ? film.poster_url
            : "";


    const meta = [
        film.year
            ? `📅 ${escapeHtml(
                film.year
            )}`
            : "",

        film.quality
            ? `⚙️ ${escapeHtml(
                film.quality
            )}`
            : "",

        film.genre
            ? `🎭 ${escapeHtml(
                film.genre
            )}`
            : ""
    ]
    .filter(Boolean)
    .join(" · ");


    return `
        <div
            class="film-card"
            data-film-id="${film.id}"
        >

            <div class="film-poster">

                ${
                    poster
                    ? `
                        <img
                            src="${poster}"
                            alt="${escapeHtml(
                                film.title
                            )}"
                            loading="lazy"
                        >
                    `
                    : `
                        <div class="poster-placeholder">
                            🎬
                        </div>
                    `
                }

            </div>


            <div class="film-info">

                <h3>
                    ${escapeHtml(
                        film.title
                    )}
                </h3>

                ${
                    meta
                    ? `
                        <div class="film-meta">
                            ${meta}
                        </div>
                    `
                    : ""
                }

                ${
                    film.official
                    ? `
                        <span class="official-badge">
                            ⭐ زموږ فلم
                        </span>
                    `
                    : ""
                }

                <button
                    class="film-button"
                    onclick="openFilm(
                        ${film.id}
                    )"
                >
                    🎬 فلم ترلاسه کړئ
                </button>

            </div>

        </div>
    `;
}


// =========================================================
// OPEN FILM
// =========================================================

async function openFilm(
    filmId
) {

    try {

        const film =
            await api(
                `/api/films/${filmId}`
            );

        showFilmDetails(
            film
        );

    } catch (error) {

        handleApiError(
            error
        );
    }
}


function showFilmDetails(
    film
) {

    let modal =
        $("filmModal");

    if (!modal) {

        modal =
            document.createElement(
                "div"
            );

        modal.id =
            "filmModal";

        modal.innerHTML = `
            <div
                class="film-modal-overlay"
                onclick="closeFilmModal(event)"
            >

                <div
                    class="film-modal"
                    onclick="event.stopPropagation()"
                >

                    <button
                        class="modal-close"
                        onclick="closeFilmModal()"
                    >
                        ✕
                    </button>

                    <div id="filmModalContent">
                    </div>

                </div>

            </div>
        `;

        document.body.appendChild(
            modal
        );
    }


    const poster =
        film.poster_url
            ? `
                <img
                    class="modal-poster"
                    src="${film.poster_url}"
                >
            `
            : `
                <div class="modal-poster-placeholder">
                    🎬
                </div>
            `;


    const details = [
        film.year
            ? `📅 کال: ${escapeHtml(
                film.year
            )}`
            : "",

        film.quality
            ? `⚙️ کیفیت: ${escapeHtml(
                film.quality
            )}`
            : "",

        film.genre
            ? `🎭 ژانر: ${escapeHtml(
                film.genre
            )}`
            : "",

        film.language
            ? `🔊 ژبه: ${escapeHtml(
                film.language
            )}`
            : ""
    ]
    .filter(Boolean)
    .map(
        item =>
            `<div>${item}</div>`
    )
    .join("");


    $("filmModalContent").innerHTML = `

        ${poster}

        <h2>
            ${escapeHtml(
                film.title
            )}
        </h2>

        <div class="modal-details">
            ${details}
        </div>

        ${
            film.description
            ? `
                <p class="modal-description">
                    ${escapeHtml(
                        film.description
                    )}
                </p>
            `
            : ""
        }

        <button
            class="download-button"
            onclick="downloadFilm(
                ${film.id}
            )"
        >
            🎬 فلم ترلاسه / Download
        </button>

    `;


    modal.classList.remove(
        "hidden"
    );
}


function closeFilmModal(
    event
) {

    if (
        event &&
        event.target &&
        !event.target.classList.contains(
            "film-modal-overlay"
        )
    ) {
        return;
    }

    const modal =
        $("filmModal");

    if (modal) {

        modal.classList.add(
            "hidden"
        );
    }
}


// =========================================================
// DOWNLOAD
// =========================================================

async function downloadFilm(
    filmId
) {

    try {

        const data =
            await api(
                `/api/films/${filmId}/download`
            );

        if (
            data.url
        ) {

            tg.openTelegramLink(
                data.url
            );

        }

    } catch (error) {

        handleApiError(
            error
        );
    }
}


// =========================================================
// REFERRAL LINK
// =========================================================

function copyReferralLink() {

    const input =
        $("referralLink");

    if (!input) {
        return;
    }

    if (
        navigator.clipboard
    ) {

        navigator.clipboard.writeText(
            input.value
        );

    } else {

        input.select();

        document.execCommand(
            "copy"
        );
    }

    tg.showPopup({
        title: "Referral Link",
        message: "ستاسو Referral Link کاپي شو.",
        buttons: [
            {
                type: "ok"
            }
        ]
    });
}


function shareReferralLink() {

    const input =
        $("referralLink");

    if (
        !input ||
        !input.value
    ) {
        return;
    }

    const text =
        "🎬 د ALL PRODUCTION FILMS سره یوځای شئ!\n\n" +
        "زما له Referral Link څخه Join شئ:\n" +
        input.value;


    const url =
        `https://t.me/share/url?url=${encodeURIComponent(
            input.value
        )}&text=${encodeURIComponent(
            text
        )}`;


    tg.openTelegramLink(
        url
    );
}


// =========================================================
// CATEGORY / TAB HELPERS
// =========================================================

async function showSection(
    sectionId
) {

    document
        .querySelectorAll(
            ".app-section"
        )
        .forEach(
            section => {

                section.classList.add(
                    "hidden"
                );
            }
        );


    const section =
        $(sectionId);

    if (section) {

        section.classList.remove(
            "hidden"
        );
    }
}


// =========================================================
// PUBLISH STATUS
// =========================================================

function showPublishStatus() {

    if (
        !currentUser
    ) {
        return;
    }

    const button =
        $("publishButton");

    if (!button) {
        return;
    }

    if (
        currentUser.can_publish
    ) {

        button.disabled =
            false;

        button.innerHTML =
            "🎬 فلم نشر کړئ";

    } else {

        button.disabled =
            true;

        const remaining =
            Math.max(
                currentUser.referral_target -
                currentUser.referral_count,
                0
            );

        button.innerHTML =
            `🔒 ${remaining} Referral پاتې`;
    }
}


// =========================================================
// REFRESH
// =========================================================

async function refreshApp() {

    try {

        await loadMe();

        showPublishStatus();

        await Promise.all([
            loadLatestFilms(),
            loadReferralInfo(),
            loadLeaders()
        ]);

    } catch (error) {

        handleApiError(
            error
        );
    }
}


// =========================================================
// API ERROR
// =========================================================

function handleApiError(
    error
) {

    console.error(
        error
    );

    if (
        error?.status === 403 &&
        error?.detail ===
            "ACCESS_REQUIRED"
    ) {

        showAccessRequired();

        return;
    }

    if (
        error?.status === 401
    ) {

        tg.showAlert(
            "د Telegram WebApp معلومات ناسم دي."
        );

        return;
    }

    tg.showAlert(
        error?.message ||
        "یوه ستونزه رامنځته شوه."
    );
}


// =========================================================
// SAFE SET TEXT
// =========================================================

function setIfExists(
    id,
    value
) {

    const element =
        $(id);

    if (element) {

        element.textContent =
            value;
    }
}


// =========================================================
// INITIALIZE
// =========================================================

async function initialize() {

    try {

        if (!initData) {

            tg.showAlert(
                "دا Mini App باید له Telegram څخه خلاص شي."
            );

            return;
        }


        const user =
            await loadMe();

        hideAccessRequired();

        showPublishStatus();


        await Promise.all([
            loadLatestFilms(),
            loadReferralInfo(),
            loadLeaders()
        ]);


    } catch (error) {

        handleApiError(
            error
        );
    }
}


// =========================================================
// GLOBAL EVENTS
// =========================================================

document.addEventListener(
    "DOMContentLoaded",
    () => {

        initialize();

    }
);


// =========================================================
// TELEGRAM BACK BUTTON
// =========================================================

try {

    tg.BackButton.onClick(
        () => {

            window.history.back();

        }
    );

} catch {
    // Ignore
}


// =========================================================
// EXPOSE FUNCTIONS
// =========================================================

window.openFilm =
    openFilm;

window.downloadFilm =
    downloadFilm;

window.closeFilmModal =
    closeFilmModal;

window.copyReferralLink =
    copyReferralLink;

window.shareReferralLink =
    shareReferralLink;

window.searchFilms =
    searchFilms;

window.loadCategory =
    loadCategory;

window.loadOfficialFilms =
    loadOfficialFilms;

window.showSection =
    showSection;
