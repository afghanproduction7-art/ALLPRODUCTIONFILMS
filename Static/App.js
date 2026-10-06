const tg = window.Telegram?.WebApp;

if (tg) {
    tg.ready();
    tg.expand();
}


const results = document.getElementById("results");
const loading = document.getElementById("loading");
const empty = document.getElementById("empty");
const sectionTitle = document.getElementById("sectionTitle");
const modal = document.getElementById("modal");
const modalBody = document.getElementById("modalBody");


function showLoading() {
    loading.classList.remove("hidden");
    empty.classList.add("hidden");
    results.innerHTML = "";
}


function hideLoading() {
    loading.classList.add("hidden");
}


function escapeHtml(text) {

    if (!text) {
        return "";
    }

    return String(text)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function renderFilms(films) {

    hideLoading();

    results.innerHTML = "";

    if (!films || films.length === 0) {

        empty.classList.remove("hidden");

        return;
    }

    empty.classList.add("hidden");


    films.forEach(film => {

        const card = document.createElement("div");

        card.className = "film-card";


        const poster = film.poster_file_id
            ? `<div class="poster-placeholder">🎬</div>`
            : `<div class="poster-placeholder">🎬</div>`;


        card.innerHTML = `

            ${poster}

            <div class="film-info">

                <h3>
                    ${escapeHtml(film.title)}
                </h3>

                <p>
                    📅 ${escapeHtml(film.year || "—")}
                </p>

                <p>
                    ⚙️ ${escapeHtml(film.quality || "—")}
                </p>

                <button
                    onclick="openFilm(${film.id})"
                >
                    ▶️ فلم وګوره
                </button>

            </div>

        `;


        results.appendChild(card);

    });
}


async function apiRequest(url, options = {}) {

    const headers = {
        "Content-Type": "application/json"
    };


    if (tg?.initData) {

        headers["X-Telegram-Init-Data"] =
            tg.initData;

    }


    const response = await fetch(
        url,
        {
            ...options,
            headers
        }
    );


    if (!response.ok) {

        throw new Error(
            `HTTP ${response.status}`
        );

    }


    return response.json();
}


async function searchFilms() {

    const input =
        document.getElementById(
            "searchInput"
        );

    const query =
        input.value.trim();


    if (query.length < 2) {

        if (tg) {
            tg.showAlert(
                "لږ تر لږه ۲ حروف ولیکئ."
            );
        }

        return;
    }


    sectionTitle.textContent =
        "🔎 د لټون پایلې";


    showLoading();


    try {

        const data =
            await apiRequest(
                `/api/films/search?q=${encodeURIComponent(query)}`
            );


        renderFilms(data.films);

    }

    catch (error) {

        hideLoading();

        console.error(error);

        if (tg) {
            tg.showAlert(
                "د لټون پر مهال ستونزه رامنځته شوه."
            );
        }

    }

}


async function showCategory(category) {

    sectionTitle.textContent =
        "🎬 پښتو ترجمه فلمونه";

    showLoading();


    try {

        const data =
            await apiRequest(
                `/api/films/category/${encodeURIComponent(category)}`
            );

        renderFilms(data.films);

    }

    catch (error) {

        hideLoading();

        console.error(error);

    }

}


async function showOfficial() {

    sectionTitle.textContent =
        "📢 زموږ فلمونه";

    showLoading();


    try {

        const data =
            await apiRequest(
                "/api/films/official"
            );

        renderFilms(data.films);

    }

    catch (error) {

        hideLoading();

        console.error(error);

    }

}


async function openFilm(id) {

    try {

        const data =
            await apiRequest(
                `/api/films/${id}`
            );


        const film =
            data.film;


        modalBody.innerHTML = `

            <h2>
                🎬 ${escapeHtml(film.title)}
            </h2>

            <div class="details">

                <p>
                    📅 کال:
                    <b>${escapeHtml(film.year || "—")}</b>
                </p>

                <p>
                    ⚙️ کیفیت:
                    <b>${escapeHtml(film.quality || "—")}</b>
                </p>

                <p>
                    🎭 ژانر:
                    <b>${escapeHtml(film.genre || "—")}</b>
                </p>

                <p>
                    🔊 ژبه:
                    <b>${escapeHtml(film.language || "—")}</b>
                </p>

            </div>

            <p>
                ${escapeHtml(film.description || "")}
            </p>

            <button
                class="download-button"
                onclick="downloadFilm(${film.id})"
            >
                📥 فلم ترلاسه کړه
            </button>

        `;


        modal.classList.remove("hidden");

    }

    catch (error) {

        console.error(error);

    }

}


async function downloadFilm(id) {

    try {

        const data =
            await apiRequest(
                `/api/films/${id}/download`
            );


        if (
            data.telegram_url &&
            tg
        ) {

            tg.openTelegramLink(
                data.telegram_url
            );

            return;
        }


        if (tg) {

            tg.showAlert(
                "فلم به د Telegram Bot له لارې درولېږل شي."
            );

        }

    }

    catch (error) {

        console.error(error);

    }

}


async function showReferral() {

    try {

        const data =
            await apiRequest(
                "/api/referral"
            );


        modalBody.innerHTML = `

            <h2>
                👥 Referral
            </h2>

            <div class="referral-box">

                <p>
                    👤 ستا راوستل شوي کسان:
                    <b>${data.count}</b>
                </p>

                <p>
                    🎯 هدف:
                    <b>${data.target}</b>
                </p>

                <p>
                    ${escapeHtml(data.status)}
                </p>

                <input
                    readonly
                    value="${escapeHtml(data.link || "")}"
                >

                <button
                    onclick="copyReferral()"
                >
                    📋 Link Copy کړه
                </button>

            </div>

        `;


        modal.classList.remove("hidden");

    }

    catch (error) {

        console.error(error);

    }

}


function copyReferral() {

    const input =
        document.querySelector(
            ".referral-box input"
        );


    if (!input) {
        return;
    }


    navigator.clipboard
        .writeText(input.value)
        .then(() => {

            if (tg) {

                tg.showAlert(
                    "✅ Referral Link Copy شو."
                );

            }

        });

}


async function showLeaders() {

    try {

        const data =
            await apiRequest(
                "/api/referral/leaders"
            );


        let html =
            "<h2>🏆 Referral Leaders</h2>";


        if (!data.leaders.length) {

            html +=
                "<p>تر اوسه معلومات نشته.</p>";

        }


        data.leaders.forEach(
            (user, index) => {

                html += `

                    <div class="leader">

                        <span>
                            #${index + 1}
                        </span>

                        <b>
                            ${escapeHtml(
                                user.name
                            )}
                        </b>

                        <strong>
                            ${user.count}
                        </strong>

                    </div>

                `;

            }
        );


        modalBody.innerHTML = html;

        modal.classList.remove("hidden");

    }

    catch (error) {

        console.error(error);

    }

}


function closeModal() {

    modal.classList.add(
        "hidden"
    );

}


window.addEventListener(
    "click",
    event => {

        if (
            event.target === modal
        ) {

            closeModal();

        }

    }
);


// Initial load
async function loadLatest() {

    showLoading();

    try {

        const data =
            await apiRequest(
                "/api/films/latest"
            );

        renderFilms(data.films);

    }

    catch (error) {

        hideLoading();

        console.error(error);

    }

}


loadLatest();
