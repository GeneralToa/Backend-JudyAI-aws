// =============================================================
// review.js — Contract Review Page
// Reads contractId + documentId from URL, fetches analysis + document.
// DOCX: rendered via BDA page images (presigned JPEG).
// PDF:  rendered via PDF.js on presigned S3 URL.
// =============================================================

// PDF.js — loaded from CDN on demand
const PDFJS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
const PDFJS_WORKER = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

let pdfDoc = null;
let currentPage = 1;
let totalPages = 0;
let currentScale = 1.0;
const SCALE_STEP = 0.25;
const MIN_SCALE = 0.5;
const MAX_SCALE = 3.0;

// Viewer mode — set once loadDocument() determines file type
// "pdf" | "bda" | null
let viewerMode = null;

// BDA image state
let bdaPages = [];        // [{page, url}] sorted by page number
let _bdaRefreshTimer = null;   // setInterval handle for 15-min URL refresh
let _bdaContractId  = null;    // stored so the refresh callback can re-fetch

// Field overlays — loaded for signer mode
let fieldOverlays = [];   // array of field objects from template-prepopulation
let hasScrolledToBottom = false;

document.addEventListener("DOMContentLoaded", () => {
    if (!isAuthenticated()) return;

    // --- Parse URL params ---
    const params = new URLSearchParams(window.location.search);
    const contractId  = params.get("contractId");
    const documentId  = params.get("documentId");
    const contractName = params.get("name") || "Contract";
    const contractStatus = params.get("status") || "uploaded";

    if (!contractId) {
        showPlaceholder("No contract selected.", "Go back and click a contract to review it.");
        return;
    }

    // --- Set document name in header ---
    document.getElementById("review-doc-name").textContent = decodeURIComponent(contractName);
    document.title = `${decodeURIComponent(contractName)} — Judy.ai`;

    // --- Back button ---
    document.getElementById("btn-back").addEventListener("click", () => {
        window.location.href = "index.html";
    });

    // --- Render header actions + action bar ---
    renderActions(contractId, contractName, contractStatus);

    // --- Load analysis (fetches pageImages) + document in one shot ---
    // loadAnalysis extracts extraction.pageImages and calls loadDocument() itself
    // so we always have filename + pageImages together before deciding viewer mode.
    const filename = decodeURIComponent(contractName);
    loadAnalysis(contractId, contractStatus, documentId, filename);

    // --- Toolbar buttons ---
    document.getElementById("btn-prev-page").addEventListener("click", () => {
        if (currentPage > 1) goToPage(currentPage - 1);
    });
    document.getElementById("btn-next-page").addEventListener("click", () => {
        if (currentPage < totalPages) goToPage(currentPage + 1);
    });
    document.getElementById("btn-zoom-in").addEventListener("click", () => {
        if (viewerMode !== "pdf") return;
        if (currentScale < MAX_SCALE) {
            currentScale = Math.min(MAX_SCALE, currentScale + SCALE_STEP);
            renderPdfPage(currentPage);
        }
    });
    document.getElementById("btn-zoom-out").addEventListener("click", () => {
        if (viewerMode !== "pdf") return;
        if (currentScale > MIN_SCALE) {
            currentScale = Math.max(MIN_SCALE, currentScale - SCALE_STEP);
            renderPdfPage(currentPage);
        }
    });
});

// =============================================================
// Page navigation — works for both PDF and BDA modes
// =============================================================
function goToPage(pageNum) {
    if (viewerMode === "pdf") {
        renderPdfPage(pageNum);
    } else if (viewerMode === "bda") {
        scrollBdaToPage(pageNum);
    }
}

// =============================================================
// Load Analysis — fetches ?include=pageImages, then kicks off
// the summary/risk calls and document loading in parallel.
// Task #1
// =============================================================
async function loadAnalysis(contractId, contractStatus, documentId, filename) {
    const loadingEl = document.getElementById("review-ai-loading");
    const contentEl = document.getElementById("review-ai-content");
    const errorEl   = document.getElementById("review-ai-error");
    const pendingEl = document.getElementById("review-ai-pending");

    // --- Step 1: fetch pageImages independently — failure here must not block the AI panel ---
    let pageImages = [];
    try {
        const statusRes = await fetch(
            `${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis?include=pageImages`,
            { headers: { Authorization: getIdToken() } }
        );
        if (statusRes.ok) {
            const statusData = await statusRes.json();
            // Shape: { extraction: { id, pageImages: [{page, url}], ... }, agents: {...} }
            pageImages = statusData?.extraction?.pageImages || [];
        }
    } catch (_) {
        // analysis-api not yet redeployed with ?include=pageImages, or network hiccup.
        // Document viewer falls back to showPlaceholder / PDF.js — AI panel still loads.
    }

    // Kick off document loading now that we have (possibly empty) pageImages
    loadDocument(contractId, documentId, filename, pageImages);

    // --- Step 2: fetch summary + risks — these drive the AI panel ---
    try {
        const [summaryRes, riskRes] = await Promise.all([
            fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis/summary`, {
                headers: { Authorization: getIdToken() },
            }),
            fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis/risk-clause`, {
                headers: { Authorization: getIdToken() },
            }),
        ]);

        // Handle 409 — analysis still running
        if (summaryRes.status === 409 || riskRes.status === 409) {
            loadingEl.classList.add("hidden");
            pendingEl.classList.remove("hidden");
            return;
        }

        if (!summaryRes.ok && !riskRes.ok) {
            throw new Error("Failed to load analysis data.");
        }

        const summaryData = summaryRes.ok ? await summaryRes.json() : null;
        const riskData    = riskRes.ok    ? await riskRes.json()    : null;

        loadingEl.classList.add("hidden");
        contentEl.classList.remove("hidden");

        // Also fetch template type from template-prepopulation for the badge
        fetchTemplateBadge(contractId);

        renderSummary(summaryData);
        renderRisks(riskData, contractStatus);

    } catch (err) {
        loadingEl.classList.add("hidden");
        errorEl.classList.remove("hidden");
        document.getElementById("review-ai-error-msg").textContent = err.message;
    }
}

async function fetchTemplateBadge(contractId) {
    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis/template-prepopulation`, {
            headers: { Authorization: getIdToken() },
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.detectedTemplateType && data.detectedTemplateType !== "unknown") {
            const badge = document.getElementById("review-template-type");
            badge.textContent = data.detectedTemplateType.toUpperCase();
            badge.classList.remove("hidden");
        }
    } catch (_) {
        // non-critical
    }
}

// =============================================================
// Document Loading — branches on file type + pageImages
// Task #2
// =============================================================
async function loadDocument(contractId, documentId, filename, pageImages) {
    const ext = (filename.split(".").pop() || "").toLowerCase();

    // DOCX with BDA images → render image stack
    if ((ext === "docx" || ext === "doc") && pageImages.length > 0) {
        // Sort pages ascending
        bdaPages = [...pageImages].sort((a, b) => a.page - b.page);
        _bdaContractId = contractId;
        renderBdaImages(bdaPages);
        return;
    }

    // PDF, or DOCX with no BDA images (extraction pending / failed) → PDF.js
    if (!documentId) {
        showPlaceholder("Document not available", "No document ID provided.");
        return;
    }

    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/files/${documentId}/download`, {
            headers: { Authorization: getIdToken() },
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showPlaceholder("Could not load document", err.error || `Error ${res.status}`);
            return;
        }

        const data = await res.json();
        const url  = data.download_url;

        if (ext === "pdf") {
            loadPdf(url);
        } else {
            // DOCX with no BDA images — fall back to PDF.js (Zuhair: PDFs have no page images;
            // for DOCXs this means extraction hasn't completed yet, try PDF.js anyway).
            loadPdf(url);
        }

    } catch (err) {
        showPlaceholder("Could not load document", err.message);
    }
}

// =============================================================
// BDA Image Renderer — DOCX pages as stacked <img> elements
// Task #3
// =============================================================
function renderBdaImages(pages) {
    viewerMode = "bda";
    totalPages = pages.length;
    currentPage = 1;

    // Hide placeholder + canvas; show BDA container
    document.getElementById("review-doc-placeholder").classList.add("hidden");
    document.getElementById("review-pdf-canvas").classList.add("hidden");
    document.getElementById("review-docx-iframe").classList.add("hidden");

    // Build (or reuse) the BDA image container
    let bdaContainer = document.getElementById("review-bda-container");
    if (!bdaContainer) {
        bdaContainer = document.createElement("div");
        bdaContainer.id = "review-bda-container";
        bdaContainer.className = "review-bda-container";
        document.getElementById("review-doc-viewport").appendChild(bdaContainer);
    }
    bdaContainer.innerHTML = "";

    // Render each page as a wrapper div (relative) + img + overlay layer
    pages.forEach(({ page, url }) => {
        const wrapper = document.createElement("div");
        wrapper.className = "review-bda-page";
        wrapper.dataset.page = page;

        // The img — URLs are presigned and served as image/jpeg despite .png in the key name
        const img = document.createElement("img");
        img.src = url;
        img.alt = `Page ${page}`;
        img.className = "review-bda-img";
        img.draggable = false;

        // Overlay layer — sits on top of the image; field boxes injected here
        // Task #4: overlay divs positioned absolutely using normalized coordinates
        const overlayLayer = document.createElement("div");
        overlayLayer.className = "review-bda-overlay";
        overlayLayer.dataset.page = page;

        // Update page indicator when image scrolls into view
        img.addEventListener("load", () => {
            // Draw field overlays once we know the rendered image dimensions
            drawBdaFieldOverlays(overlayLayer, page, img);
        });

        wrapper.appendChild(img);
        wrapper.appendChild(overlayLayer);
        bdaContainer.appendChild(wrapper);
    });

    // Enable nav buttons
    document.getElementById("btn-prev-page").disabled = false;
    document.getElementById("btn-next-page").disabled = false;

    // Disable zoom for BDA mode (images are fixed resolution)
    document.getElementById("btn-zoom-in").disabled = true;
    document.getElementById("btn-zoom-out").disabled = true;
    document.getElementById("zoom-level").textContent = "—";

    updatePageIndicator();

    // Track current page as user scrolls through the image stack
    // Also drives the scroll gate for signer mode
    // Task #6
    const viewport = document.getElementById("review-doc-viewport");
    viewport.addEventListener("scroll", onViewportScroll, { passive: true });

    // --- 15-min URL refresh ---
    // Zuhair: presigned pageImages URLs expire after 15 min.
    // Re-fetch ?include=pageImages at 14 min and swap each img.src in-place.
    // Clear any previous timer first (in case renderBdaImages is called again).
    if (_bdaRefreshTimer) clearInterval(_bdaRefreshTimer);
    const REFRESH_MS = 14 * 60 * 1000; // 14 min — refresh 1 min before expiry
    _bdaRefreshTimer = setInterval(async () => {
        if (!_bdaContractId) return;
        try {
            const res = await fetch(
                `${CONFIG.API_BASE_URL}/contracts/${_bdaContractId}/analysis?include=pageImages`,
                { headers: { Authorization: getIdToken() } }
            );
            if (!res.ok) return;
            const data = await res.json();
            const freshPages = data?.extraction?.pageImages || [];
            if (!freshPages.length) return;

            // Swap src on each rendered img without re-rendering the whole stack
            freshPages.forEach(({ page, url }) => {
                const wrapper = document.querySelector(`.review-bda-page[data-page="${page}"]`);
                if (wrapper) {
                    const img = wrapper.querySelector(".review-bda-img");
                    if (img && img.src !== url) img.src = url;
                }
            });
            // Update bdaPages so any subsequent re-render uses fresh URLs
            bdaPages = [...freshPages].sort((a, b) => a.page - b.page);
        } catch (_) {
            // Refresh failure is non-fatal — images will 403 after expiry but
            // we'll retry on the next interval tick.
        }
    }, REFRESH_MS);
}

// Scroll to a specific BDA page by page number
function scrollBdaToPage(pageNum) {
    const wrapper = document.querySelector(`.review-bda-page[data-page="${pageNum}"]`);
    if (wrapper) {
        wrapper.scrollIntoView({ behavior: "smooth", block: "start" });
        currentPage = pageNum;
        updatePageIndicator();
        updateNavButtons();
    }
}

// =============================================================
// Field Overlays — BDA image mode
// Task #4
// =============================================================
function drawBdaFieldOverlays(overlayLayer, pageNum, img) {
    if (!fieldOverlays.length) return;

    const pageFields = fieldOverlays.filter(f => f.page === pageNum && f.position);
    if (!pageFields.length) return;

    // Zuhair's spec: coordinates are normalized to the image's natural pixel dimensions,
    // so we verify the image has decoded before drawing. Positioning itself uses CSS %
    // because the overlay div always matches the img size.
    const imgW = img.naturalWidth;
    const imgH = img.naturalHeight;
    if (!imgW || !imgH) return;

    pageFields.forEach(f => {
        const { x, y, width, height } = f.position;

        const box = document.createElement("div");
        box.className = "review-bda-field-box";
        box.style.left   = `${x * 100}%`;
        box.style.top    = `${y * 100}%`;
        box.style.width  = `${width * 100}%`;
        box.style.height = `${height * 100}%`;

        if (f.fieldType) {
            const label = document.createElement("span");
            label.className = "review-bda-field-label";
            label.textContent = f.fieldType;
            box.appendChild(label);
        }

        overlayLayer.appendChild(box);
    });
}

// Redraw overlays after window resize (image dimensions may change)
function redrawAllBdaOverlays() {
    document.querySelectorAll(".review-bda-page").forEach(wrapper => {
        const page = parseInt(wrapper.dataset.page, 10);
        const overlayLayer = wrapper.querySelector(".review-bda-overlay");
        const img = wrapper.querySelector(".review-bda-img");
        if (overlayLayer && img) {
            overlayLayer.innerHTML = "";
            drawBdaFieldOverlays(overlayLayer, page, img);
        }
    });
}

// Debounced resize listener (only in BDA mode)
let _resizeTimer;
window.addEventListener("resize", () => {
    if (viewerMode !== "bda") return;
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(redrawAllBdaOverlays, 200);
});

// =============================================================
// Scroll handler — page tracking + scroll gate (Tasks #5, #6)
// =============================================================
function onViewportScroll() {
    const viewport = document.getElementById("review-doc-viewport");

    // Update current page indicator by finding which page is most visible
    if (viewerMode === "bda") {
        const pages = document.querySelectorAll(".review-bda-page");
        const midY = viewport.scrollTop + viewport.clientHeight / 2;
        let closestPage = 1;
        let closestDist = Infinity;
        pages.forEach(wrapper => {
            const top = wrapper.offsetTop;
            const dist = Math.abs(top + wrapper.offsetHeight / 2 - midY);
            if (dist < closestDist) {
                closestDist = dist;
                closestPage = parseInt(wrapper.dataset.page, 10);
            }
        });
        if (closestPage !== currentPage) {
            currentPage = closestPage;
            updatePageIndicator();
            updateNavButtons();
        }
    }

    // Scroll gate — unlock Sign Document when near bottom
    if (!hasScrolledToBottom) {
        const atBottom = viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 60;
        if (atBottom) {
            hasScrolledToBottom = true;
            const signBtn = document.getElementById("btn-sign-doc");
            const hint    = document.getElementById("scroll-gate-hint");
            if (signBtn) {
                signBtn.disabled = false;
                signBtn.style.opacity = "1";
            }
            if (hint) hint.textContent = "You've reviewed the document. You can now sign.";
        }
    }
}

// =============================================================
// PDF via PDF.js
// =============================================================
function loadPdf(url) {
    viewerMode = "pdf";

    const script = document.createElement("script");
    script.src = PDFJS_CDN;
    script.onload = () => {
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;

        showPlaceholder("Loading document...", "");

        window.pdfjsLib.getDocument({ url }).promise.then(pdf => {
            pdfDoc = pdf;
            totalPages = pdf.numPages;
            currentPage = 1;

            document.getElementById("review-doc-placeholder").classList.add("hidden");
            document.getElementById("review-pdf-canvas").classList.remove("hidden");

            document.getElementById("btn-prev-page").disabled = false;
            document.getElementById("btn-next-page").disabled = false;
            document.getElementById("btn-zoom-in").disabled = false;
            document.getElementById("btn-zoom-out").disabled = false;

            updatePageIndicator();
            renderPdfPage(currentPage);

            // Wire PDF viewport scroll for scroll gate
            const viewport = document.getElementById("review-doc-viewport");
            viewport.addEventListener("scroll", onViewportScroll, { passive: true });

        }).catch(err => {
            showPlaceholder("Could not render PDF", err.message);
        });
    };
    script.onerror = () => showPlaceholder("Could not load PDF viewer", "PDF.js failed to load.");
    document.head.appendChild(script);
}

function renderPdfPage(pageNum) {
    if (!pdfDoc) return;

    pdfDoc.getPage(pageNum).then(page => {
        const canvas = document.getElementById("review-pdf-canvas");
        const ctx    = canvas.getContext("2d");

        const viewport = page.getViewport({ scale: currentScale });
        canvas.width  = viewport.width;
        canvas.height = viewport.height;

        page.render({ canvasContext: ctx, viewport }).promise.then(() => {
            currentPage = pageNum;
            updatePageIndicator();
            updateZoomLabel();

            // Draw field overlays for signer mode
            drawPdfFieldOverlays(ctx, canvas, pageNum);

            updateNavButtons();
        });
    });
}

// =============================================================
// Field Overlays — PDF canvas mode
// =============================================================
async function loadFieldOverlays(contractId) {
    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis/template-prepopulation`, {
            headers: { Authorization: getIdToken() },
        });
        if (!res.ok) return;
        const data = await res.json();
        fieldOverlays = data.fields || [];

        // If BDA viewer is already rendered, draw overlays now
        if (viewerMode === "bda") {
            redrawAllBdaOverlays();
        }
    } catch (_) {
        // non-critical
    }
}

function drawPdfFieldOverlays(ctx, canvas, pageNum) {
    if (!fieldOverlays.length) return;

    const pageFields = fieldOverlays.filter(f => f.page === pageNum && f.position);

    pageFields.forEach(f => {
        const { x, y, width, height } = f.position;

        const boxX = x      * canvas.width;
        const boxY = y      * canvas.height;
        const boxW = width  * canvas.width;
        const boxH = height * canvas.height;

        ctx.save();
        ctx.strokeStyle = "rgba(124, 77, 255, 0.8)";
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 3]);
        ctx.fillStyle = "rgba(124, 77, 255, 0.08)";
        ctx.fillRect(boxX, boxY, boxW, boxH);
        ctx.strokeRect(boxX, boxY, boxW, boxH);

        if (f.fieldType) {
            ctx.setLineDash([]);
            ctx.fillStyle = "rgba(124, 77, 255, 0.85)";
            const fontSize = Math.max(9, Math.min(12, boxH * 0.5));
            ctx.font = `600 ${fontSize}px Inter, sans-serif`;
            ctx.fillText(f.fieldType, boxX + 4, boxY + fontSize + 2);
        }

        ctx.restore();
    });
}

// =============================================================
// UI helpers
// =============================================================
function updatePageIndicator() {
    document.getElementById("page-num").textContent   = currentPage;
    document.getElementById("page-count").textContent = totalPages;
}

function updateZoomLabel() {
    document.getElementById("zoom-level").textContent = `${Math.round(currentScale * 100)}%`;
}

function updateNavButtons() {
    document.getElementById("btn-prev-page").disabled = currentPage <= 1;
    document.getElementById("btn-next-page").disabled = currentPage >= totalPages;
}

// =============================================================
// Actions — header right + bottom bar based on contract status
// =============================================================
function renderActions(contractId, contractName, contractStatus) {
    const headerActions = document.getElementById("review-header-actions");
    const actionBar     = document.getElementById("review-action-bar");

    const metaEl = document.getElementById("review-doc-meta");
    metaEl.innerHTML = `<span class="contract-status ${contractStatus}" style="font-size:0.7rem;padding:2px 8px;">${contractStatus.replace(/_/g, " ")}</span>`;

    if (contractStatus === "uploaded" || contractStatus === "analyzing" || contractStatus === "ready_for_review") {
        headerActions.innerHTML = "";

        actionBar.innerHTML = `
            <span id="action-bar-risk" class="action-bar-risk hidden"></span>
            <button id="btn-send-for-sig" class="btn-primary" style="min-width:180px">
                <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="22" y1="2" x2="11" y2="13"/>
                    <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
                Send for Signature
            </button>`;

        document.getElementById("btn-send-for-sig").addEventListener("click", () => {
            openRouteModal(contractId, decodeURIComponent(contractName));
        });

    } else if (contractStatus === "routed_for_signature") {
        headerActions.innerHTML = `
            <button id="btn-decline" class="btn-secondary btn-sm" style="color:var(--color-error);border-color:rgba(239,83,80,0.3)">
                Decline
            </button>`;

        actionBar.innerHTML = `
            <p id="scroll-gate-hint" style="font-size:0.78rem;color:var(--text-muted);margin-right:auto">
                📄 Scroll through the document to enable signing.
            </p>
            <button id="btn-sign-doc" class="btn-primary" style="min-width:140px" disabled>
                <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
                </svg>
                Sign Document
            </button>`;

        document.getElementById("btn-decline").addEventListener("click", () => {
            showToast("Decline flow coming soon.", "info");
        });

        document.getElementById("btn-sign-doc").addEventListener("click", () => {
            openReviewSignatureModal(contractId, decodeURIComponent(contractName));
        });

        // Load field overlays — they'll be drawn once viewer is ready
        loadFieldOverlays(contractId);

        // Note: scroll event is attached inside renderBdaImages() / loadPdf()
        // so it fires after the viewer is set up. Both call onViewportScroll()
        // which handles the scroll gate.

    } else if (contractStatus === "signed") {
        headerActions.innerHTML = `<span class="contract-status signed" style="font-size:0.78rem;padding:4px 12px">✓ Signed</span>`;
        actionBar.innerHTML = "";
    }
}

// =============================================================
// Render Summary
// =============================================================
function renderSummary(data) {
    const summaryEl   = document.getElementById("review-summary-text");
    const keyPointsEl = document.getElementById("review-key-points");

    if (!data || !data.summaryText) {
        summaryEl.textContent = "Summary not available for this contract.";
        summaryEl.style.fontStyle = "italic";
        summaryEl.style.color = "var(--text-muted)";
        return;
    }

    summaryEl.textContent = data.summaryText;

    const keyPoints = data.keyPoints || [];
    if (keyPoints.length > 0) {
        keyPointsEl.innerHTML = keyPoints.map(kp => `<li>${escapeHtml(kp)}</li>`).join("");
        keyPointsEl.classList.remove("hidden");
    }
}

// =============================================================
// Render Risks
// =============================================================
function renderRisks(data, contractStatus) {
    const risksList    = document.getElementById("review-risks-list");
    const riskBadge    = document.getElementById("review-risk-badge");
    const actionBarRisk = document.getElementById("action-bar-risk");

    const risks = data?.risks || [];

    if (risks.length > 0) {
        riskBadge.textContent = `${risks.length} risk${risks.length !== 1 ? "s" : ""}`;
        riskBadge.classList.remove("hidden");
    }

    if (actionBarRisk && risks.length > 0) {
        actionBarRisk.innerHTML = `<span class="risk-count-badge">⚠ ${risks.length} risk${risks.length !== 1 ? "s" : ""}</span>`;
        actionBarRisk.classList.remove("hidden");
    }

    if (!risks.length) {
        risksList.innerHTML = `
            <div class="review-no-risks">
                <div class="review-no-risks-icon">✅</div>
                <p>No risks flagged for this contract.</p>
            </div>`;
        return;
    }

    risksList.innerHTML = risks.map(r => `
        <div class="review-risk-item">
            <div class="review-risk-item-header">
                <span class="review-severity review-severity-${r.severity}">${r.severity}</span>
                <span class="review-risk-category">${formatRiskCategory(r.category)}</span>
            </div>
            <div class="review-risk-title">${escapeHtml(r.title)}</div>
            <p class="review-risk-detail">${escapeHtml(r.detail)}</p>
            ${r.sourceQuote ? `
                <blockquote class="review-risk-quote">
                    "${escapeHtml(r.sourceQuote)}"
                    ${r.sourcePage ? `<span class="review-risk-page">p.${r.sourcePage}</span>` : ""}
                </blockquote>` : ""}
            ${r.suggestedLanguage ? `
                <div class="review-risk-suggestion">
                    <span class="review-risk-suggestion-label">💡 Advisory suggestion — not applied to document</span>
                    <p class="review-risk-suggestion-text">${escapeHtml(r.suggestedLanguage)}</p>
                </div>` : ""}
        </div>
    `).join("");
}

// =============================================================
// Route Modal (Send for Signature)
// =============================================================
function openRouteModal(contractId, contractName) {
    const routeModal = document.getElementById("route-modal");
    document.getElementById("route-modal-filename").textContent = contractName;
    document.getElementById("signer-1-email").value = "";
    document.getElementById("signer-2-email").value = "";
    routeModal.classList.remove("hidden");

    document.getElementById("route-modal-cancel").onclick = () => routeModal.classList.add("hidden");
    routeModal.onclick = (e) => { if (e.target === routeModal) routeModal.classList.add("hidden"); };

    document.getElementById("route-modal-confirm").onclick = async () => {
        const signer1 = document.getElementById("signer-1-email").value.trim();
        const signer2 = document.getElementById("signer-2-email").value.trim();

        if (!signer1 || !signer2) {
            showToast("Please provide both signer emails", "error");
            return;
        }

        const confirmBtn = document.getElementById("route-modal-confirm");
        confirmBtn.disabled = true;
        confirmBtn.textContent = "Sending...";

        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/route`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Authorization: getIdToken(),
                },
                body: JSON.stringify({
                    signers: [
                        { email: signer1, role: "supplier" },
                        { email: signer2, role: "company" },
                    ],
                }),
            });

            if (!res.ok) throw new Error("Failed to route contract");

            routeModal.classList.add("hidden");
            showToast("Contract sent for signature!", "success");

            setTimeout(() => { window.location.href = "index.html?section=contracts&action=signed"; }, 1200);

        } catch (err) {
            showToast(`Error: ${err.message}`, "error");
        } finally {
            confirmBtn.disabled = false;
            confirmBtn.textContent = "Send for Signature";
        }
    };
}

// =============================================================
// Document Placeholder
// =============================================================
function showPlaceholder(title, sub) {
    const placeholder = document.getElementById("review-doc-placeholder");
    if (placeholder) {
        document.getElementById("placeholder-text").textContent = title;
        document.getElementById("placeholder-sub").textContent  = sub;
        placeholder.classList.remove("hidden");
    }
}

// =============================================================
// Utilities
// =============================================================
function formatRiskCategory(cat) {
    const map = {
        unusualTerm:   "Unusual Term",
        missingClause: "Missing Clause",
        dateMismatch:  "Date Mismatch",
        complianceGap: "Compliance Gap",
        trackedTerm:   "Tracked Term",
    };
    return map[cat] || cat || "";
}

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(20px)";
        toast.style.transition = "all 0.3s ease";
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// =============================================================
// Signature Modal — Draw + Type canvas + POST /sign
// =============================================================
(function () {
    const modal       = document.getElementById("review-signature-modal");
    const drawCanvas  = document.getElementById("review-signature-canvas");
    const typeCanvas  = document.getElementById("review-signature-type-canvas");
    const drawCtx     = drawCanvas.getContext("2d");
    const typeCtx     = typeCanvas.getContext("2d");
    const btnClear    = document.getElementById("review-btn-clear-signature");
    const btnCancel   = document.getElementById("review-signature-modal-cancel");
    const btnConfirm  = document.getElementById("review-signature-modal-confirm");
    const sigTextInput = document.getElementById("review-signature-text");

    let isDrawing    = false;
    let activeTab    = "draw";
    let selectedFont = "Dancing Script";
    let _contractId  = null;

    // --- Tab switching ---
    document.querySelectorAll("#review-signature-modal .sig-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll("#review-signature-modal .sig-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            activeTab = tab.dataset.tab;
            document.getElementById("review-sig-tab-draw").classList.toggle("hidden", activeTab !== "draw");
            document.getElementById("review-sig-tab-type").classList.toggle("hidden", activeTab !== "type");
        });
    });

    // --- Draw canvas helpers ---
    function clearDrawCanvas() {
        drawCtx.clearRect(0, 0, drawCanvas.width, drawCanvas.height);
        drawCtx.fillStyle = "#fff";
        drawCtx.fillRect(0, 0, drawCanvas.width, drawCanvas.height);
    }

    function getPos(e, canvas) {
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width  / rect.width;
        const scaleY = canvas.height / rect.height;
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        const clientY = e.touches ? e.touches[0].clientY : e.clientY;
        return [(clientX - rect.left) * scaleX, (clientY - rect.top) * scaleY];
    }

    drawCanvas.addEventListener("mousedown",  e => { isDrawing = true; drawCtx.beginPath(); drawCtx.moveTo(...getPos(e, drawCanvas)); });
    drawCanvas.addEventListener("mousemove",  e => { if (!isDrawing) return; drawCtx.lineTo(...getPos(e, drawCanvas)); drawCtx.strokeStyle = "#1a1a2e"; drawCtx.lineWidth = 2; drawCtx.lineCap = "round"; drawCtx.stroke(); });
    drawCanvas.addEventListener("mouseup",    () => isDrawing = false);
    drawCanvas.addEventListener("mouseleave", () => isDrawing = false);
    drawCanvas.addEventListener("touchstart", e => { e.preventDefault(); isDrawing = true; drawCtx.beginPath(); drawCtx.moveTo(...getPos(e, drawCanvas)); }, { passive: false });
    drawCanvas.addEventListener("touchmove",  e => { e.preventDefault(); if (!isDrawing) return; drawCtx.lineTo(...getPos(e, drawCanvas)); drawCtx.strokeStyle = "#1a1a2e"; drawCtx.lineWidth = 2; drawCtx.lineCap = "round"; drawCtx.stroke(); }, { passive: false });
    drawCanvas.addEventListener("touchend",   () => isDrawing = false);

    btnClear.addEventListener("click", clearDrawCanvas);

    // --- Type canvas helpers ---
    function clearTypeCanvas() {
        typeCtx.clearRect(0, 0, typeCanvas.width, typeCanvas.height);
        typeCtx.fillStyle = "#fff";
        typeCtx.fillRect(0, 0, typeCanvas.width, typeCanvas.height);
    }

    function renderTypeSignature() {
        clearTypeCanvas();
        const text = sigTextInput.value.trim();
        if (!text) return;
        typeCtx.fillStyle = "#fff";
        typeCtx.fillRect(0, 0, typeCanvas.width, typeCanvas.height);
        typeCtx.fillStyle = "#1a1a2e";
        typeCtx.font = `48px '${selectedFont}', cursive`;
        typeCtx.textAlign = "center";
        typeCtx.textBaseline = "middle";
        typeCtx.fillText(text, typeCanvas.width / 2, typeCanvas.height / 2);
    }

    sigTextInput.addEventListener("input", renderTypeSignature);

    document.querySelectorAll("#review-signature-modal .sig-font-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("#review-signature-modal .sig-font-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            selectedFont = btn.dataset.font;
            renderTypeSignature();
        });
    });

    // --- Modal open/close ---
    btnCancel.addEventListener("click", () => modal.classList.add("hidden"));
    modal.addEventListener("click", e => { if (e.target === modal) modal.classList.add("hidden"); });

    // --- Sign confirm ---
    btnConfirm.addEventListener("click", async () => {
        let signatureData;

        if (activeTab === "draw") {
            signatureData = drawCanvas.toDataURL("image/png");
        } else {
            if (!sigTextInput.value.trim()) {
                showToast("Please type your name", "error");
                return;
            }
            signatureData = typeCanvas.toDataURL("image/png");
        }

        btnConfirm.disabled = true;
        btnConfirm.textContent = "Signing...";

        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${_contractId}/sign`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Authorization: getIdToken(),
                },
                body: JSON.stringify({ signatureData }),
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.error || `Error ${res.status}`);
            }

            const data = await res.json();
            modal.classList.add("hidden");

            if (data.status === "signed") {
                showToast("Contract fully signed!", "success");
            } else {
                showToast(`Signature recorded. ${data.pendingSigners} signer(s) remaining.`, "success");
            }

            setTimeout(() => { window.location.href = "index.html?section=contracts&action=signed"; }, 1500);

        } catch (err) {
            showToast(`Error: ${err.message}`, "error");
        } finally {
            btnConfirm.disabled = false;
            btnConfirm.textContent = "Sign Document";
        }
    });

    // Exposed to renderActions()
    window.openReviewSignatureModal = function (contractId, filename) {
        _contractId = contractId;
        document.getElementById("review-signature-modal-filename").textContent = filename;
        // Reset to Draw tab
        activeTab = "draw";
        document.querySelectorAll("#review-signature-modal .sig-tab").forEach(t => t.classList.remove("active"));
        document.querySelector("#review-signature-modal .sig-tab[data-tab='draw']").classList.add("active");
        document.getElementById("review-sig-tab-draw").classList.remove("hidden");
        document.getElementById("review-sig-tab-type").classList.add("hidden");
        clearDrawCanvas();
        clearTypeCanvas();
        sigTextInput.value = "";
        modal.classList.remove("hidden");
    };
})();
