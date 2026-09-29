// =============================================================
// review.js — Contract Review Page
// Reads contractId + documentId from URL, fetches analysis + document.
// PDF rendered via PDF.js. DOCX via Office Online iframe.
// =============================================================

// PDF.js — loaded from CDN
const PDFJS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
const PDFJS_WORKER = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

let pdfDoc = null;
let currentPage = 1;
let totalPages = 0;
let currentScale = 1.0;
const SCALE_STEP = 0.25;
const MIN_SCALE = 0.5;
const MAX_SCALE = 3.0;

// Field overlays — loaded for signer mode
let fieldOverlays = []; // array of field objects from template-prepopulation
let hasScrolledToBottom = false; // scroll gate for signer

document.addEventListener("DOMContentLoaded", () => {
    if (!isAuthenticated()) return;

    // --- Parse URL params ---
    const params = new URLSearchParams(window.location.search);
    const contractId = params.get("contractId");
    const documentId = params.get("documentId");
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

    // --- Load analysis ---
    loadAnalysis(contractId, contractStatus);

    // --- Load document ---
    if (documentId) {
        loadDocument(documentId, decodeURIComponent(contractName));
    } else {
        showPlaceholder(
            "Document viewer unavailable",
            "Document ID not found. Try navigating from the Contracts list."
        );
    }

    // --- Toolbar buttons ---
    document.getElementById("btn-prev-page").addEventListener("click", () => {
        if (currentPage > 1) renderPage(--currentPage);
    });
    document.getElementById("btn-next-page").addEventListener("click", () => {
        if (currentPage < totalPages) renderPage(++currentPage);
    });
    document.getElementById("btn-zoom-in").addEventListener("click", () => {
        if (currentScale < MAX_SCALE) {
            currentScale = Math.min(MAX_SCALE, currentScale + SCALE_STEP);
            renderPage(currentPage);
        }
    });
    document.getElementById("btn-zoom-out").addEventListener("click", () => {
        if (currentScale > MIN_SCALE) {
            currentScale = Math.max(MIN_SCALE, currentScale - SCALE_STEP);
            renderPage(currentPage);
        }
    });
});

// =============================================================
// Document Loading
// =============================================================
async function loadDocument(documentId, filename) {
    try {
        // Fetch presigned URL from backend
        const res = await fetch(`${CONFIG.API_BASE_URL}/files/${documentId}/download`, {
            headers: { Authorization: getIdToken() },
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showPlaceholder("Could not load document", err.error || `Error ${res.status}`);
            return;
        }

        const data = await res.json();
        const url = data.download_url;

        // Determine file type from filename
        const ext = filename.split(".").pop().toLowerCase();

        if (ext === "pdf") {
            loadPdf(url);
        } else if (ext === "docx" || ext === "doc") {
            loadDocx(url);
        } else {
            // Try PDF first, fallback to iframe
            loadPdf(url);
        }

    } catch (err) {
        showPlaceholder("Could not load document", err.message);
    }
}

// --- PDF via PDF.js ---
function loadPdf(url) {
    const script = document.createElement("script");
    script.src = PDFJS_CDN;
    script.onload = () => {
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;

        showPlaceholder("Loading document...", "");

        window.pdfjsLib.getDocument({ url }).promise.then(pdf => {
            pdfDoc = pdf;
            totalPages = pdf.numPages;
            currentPage = 1;

            // Hide placeholder, show canvas
            document.getElementById("review-doc-placeholder").classList.add("hidden");
            document.getElementById("review-pdf-canvas").classList.remove("hidden");

            // Enable nav buttons
            document.getElementById("btn-prev-page").disabled = false;
            document.getElementById("btn-next-page").disabled = false;

            updatePageIndicator();
            renderPage(currentPage);
        }).catch(err => {
            showPlaceholder("Could not render PDF", err.message);
        });
    };
    script.onerror = () => showPlaceholder("Could not load PDF viewer", "PDF.js failed to load.");
    document.head.appendChild(script);
}

function renderPage(pageNum) {
    if (!pdfDoc) return;

    pdfDoc.getPage(pageNum).then(page => {
        const canvas = document.getElementById("review-pdf-canvas");
        const ctx = canvas.getContext("2d");

        const viewport = page.getViewport({ scale: currentScale });
        canvas.width = viewport.width;
        canvas.height = viewport.height;

        page.render({ canvasContext: ctx, viewport }).promise.then(() => {
            currentPage = pageNum;
            updatePageIndicator();
            updateZoomLabel();

            // Draw field overlays for signer mode
            drawFieldOverlays(ctx, canvas, pageNum);

            // Update nav button states
            document.getElementById("btn-prev-page").disabled = currentPage <= 1;
            document.getElementById("btn-next-page").disabled = currentPage >= totalPages;
        });
    });
}

// =============================================================
// Field Overlays — Step 5 (signer mode)
// =============================================================
async function loadFieldOverlays(contractId) {
    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis/template-prepopulation`, {
            headers: { Authorization: getIdToken() },
        });
        if (!res.ok) return;
        const data = await res.json();
        fieldOverlays = data.fields || [];
    } catch (_) {
        // non-critical
    }
}

function drawFieldOverlays(ctx, canvas, pageNum) {
    if (!fieldOverlays.length) return;

    // Filter fields for this page (1-indexed)
    const pageFields = fieldOverlays.filter(f => f.page === pageNum && f.position);

    pageFields.forEach(f => {
        const { x, y, width, height } = f.position;

        const boxX = x * canvas.width;
        const boxY = y * canvas.height;
        const boxW = width * canvas.width;
        const boxH = height * canvas.height;

        // Highlight box
        ctx.save();
        ctx.strokeStyle = "rgba(124, 77, 255, 0.8)";
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 3]);
        ctx.fillStyle = "rgba(124, 77, 255, 0.08)";
        ctx.fillRect(boxX, boxY, boxW, boxH);
        ctx.strokeRect(boxX, boxY, boxW, boxH);

        // Label (field type)
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

function updatePageIndicator() {
    document.getElementById("page-num").textContent = currentPage;
    document.getElementById("page-count").textContent = totalPages;
}

function updateZoomLabel() {
    document.getElementById("zoom-level").textContent = `${Math.round(currentScale * 100)}%`;
}

// --- DOCX via Office Online ---
function loadDocx(url) {
    document.getElementById("review-doc-placeholder").classList.add("hidden");
    const iframe = document.getElementById("review-docx-iframe");
    iframe.src = `https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(url)}`;
    iframe.classList.remove("hidden");

    // Hide page nav (not applicable for DOCX)
    document.getElementById("btn-prev-page").disabled = true;
    document.getElementById("btn-next-page").disabled = true;
    document.getElementById("page-num").textContent = "—";
    document.getElementById("page-count").textContent = "—";
}

// =============================================================
// Actions — header right + bottom bar based on contract status
// =============================================================
function renderActions(contractId, contractName, contractStatus) {
    const headerActions = document.getElementById("review-header-actions");
    const actionBar = document.getElementById("review-action-bar");

    // Status badge in header meta
    const metaEl = document.getElementById("review-doc-meta");
    metaEl.innerHTML = `<span class="contract-status ${contractStatus}" style="font-size:0.7rem;padding:2px 8px;">${contractStatus.replace(/_/g, " ")}</span>`;

    if (contractStatus === "uploaded" || contractStatus === "analyzing" || contractStatus === "ready_for_review") {
        // Owner view — can send for signature
        headerActions.innerHTML = `
            <button id="btn-download" class="btn-secondary btn-sm" disabled title="Download (coming soon)">
                <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                Download
            </button>`;

        // Risk count will be injected into the action bar after analysis loads
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
        // Signer view — read-only, must scroll before signing
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
            window.location.href = `index.html#sign-${contractId}`;
        });

        // Load field overlays from template-prepopulation
        loadFieldOverlays(contractId);

        // Scroll gate — watch the doc viewport
        const viewport = document.getElementById("review-doc-viewport");
        viewport.addEventListener("scroll", () => {
            if (hasScrolledToBottom) return;
            const atBottom = viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 40;
            if (atBottom) {
                hasScrolledToBottom = true;
                const signBtn = document.getElementById("btn-sign-doc");
                const hint = document.getElementById("scroll-gate-hint");
                if (signBtn) {
                    signBtn.disabled = false;
                    signBtn.style.opacity = "1";
                }
                if (hint) hint.textContent = "You've reviewed the document. You can now sign.";
            }
        });

    } else if (contractStatus === "signed") {
        headerActions.innerHTML = `<span class="contract-status signed" style="font-size:0.78rem;padding:4px 12px">✓ Signed</span>`;
        actionBar.innerHTML = `
            <button id="btn-view-obligations" class="btn-secondary" onclick="window.location.href='index.html'">
                View Obligations
            </button>`;
    }
}

// =============================================================
// Load Analysis — summary + risks in parallel
// =============================================================
async function loadAnalysis(contractId, contractStatus) {
    const loadingEl = document.getElementById("review-ai-loading");
    const contentEl = document.getElementById("review-ai-content");
    const errorEl = document.getElementById("review-ai-error");
    const pendingEl = document.getElementById("review-ai-pending");

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
        const riskData = riskRes.ok ? await riskRes.json() : null;

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
        // non-critical, silently ignore
    }
}

// =============================================================
// Render Summary
// =============================================================
function renderSummary(data) {
    const summaryEl = document.getElementById("review-summary-text");
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
        keyPointsEl.innerHTML = keyPoints
            .map(kp => `<li>${escapeHtml(kp)}</li>`)
            .join("");
        keyPointsEl.classList.remove("hidden");
    }
}

// =============================================================
// Render Risks
// =============================================================
function renderRisks(data, contractStatus) {
    const risksList = document.getElementById("review-risks-list");
    const riskBadge = document.getElementById("review-risk-badge");
    const actionBarRisk = document.getElementById("action-bar-risk");

    const risks = data?.risks || [];

    // Update risk badge in AI panel header
    if (risks.length > 0) {
        riskBadge.textContent = `${risks.length} risk${risks.length !== 1 ? "s" : ""}`;
        riskBadge.classList.remove("hidden");
    }

    // Update risk count on the Send for Signature button in action bar
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

            // Update status in URL and re-render actions
            setTimeout(() => {
                window.location.href = `index.html`;
            }, 1200);

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
        document.getElementById("placeholder-sub").textContent = sub;
        placeholder.classList.remove("hidden");
    }
}

// =============================================================
// Utilities
// =============================================================
function formatRiskCategory(cat) {
    const map = {
        unusualTerm: "Unusual Term",
        missingClause: "Missing Clause",
        dateMismatch: "Date Mismatch",
        complianceGap: "Compliance Gap",
        trackedTerm: "Tracked Term",
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
