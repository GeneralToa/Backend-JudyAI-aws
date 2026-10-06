// =============================================================
// Judy.ai Knowledge Base - Application Logic
// =============================================================

// Decode the current user's email from the Cognito id_token JWT payload.
// JWT structure: header.payload.signature — payload is base64url encoded JSON.
function getCurrentUserEmail() {
    try {
        const token = sessionStorage.getItem("id_token");
        if (!token) return null;
        const payload = token.split(".")[1];
        // base64url → base64 → JSON
        const decoded = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
        return (decoded.email || "").toLowerCase();
    } catch (e) {
        return null;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    if (!isAuthenticated()) return;

    // --- Navigation ---
    const navUpload = document.getElementById("nav-upload");
    const navChat = document.getElementById("nav-chat");
    const navFiles = document.getElementById("nav-files");
    const navContracts = document.getElementById("nav-contracts");
    const navDashboard = document.getElementById("nav-dashboard");
    const navAdmin = document.getElementById("nav-admin");
    const sectionUpload = document.getElementById("section-upload");
    const sectionChat = document.getElementById("section-chat");
    const sectionFiles = document.getElementById("section-files");
    const sectionContracts = document.getElementById("section-contracts");
    const sectionDashboard = document.getElementById("section-dashboard");
    const sectionAdmin = document.getElementById("section-admin");
    const btnLogout = document.getElementById("btn-logout");

    function setActiveSection(sectionName) {
        [navUpload, navChat, navFiles, navContracts, navDashboard, navAdmin].forEach(n => n && n.classList.remove("active"));
        [sectionUpload, sectionChat, sectionFiles, sectionContracts, sectionDashboard, sectionAdmin].forEach(s => s && s.classList.remove("active"));

        if (sectionName === "upload") {
            navUpload.classList.add("active");
            sectionUpload.classList.add("active");
        } else if (sectionName === "chat") {
            navChat.classList.add("active");
            sectionChat.classList.add("active");
            chatInput.focus();
        } else if (sectionName === "files") {
            navFiles.classList.add("active");
            sectionFiles.classList.add("active");
            loadFiles();
        } else if (sectionName === "contracts") {
            if (navContracts) navContracts.classList.add("active");
            if (sectionContracts) {
                sectionContracts.classList.add("active");
                const refreshBtn = document.getElementById("btn-refresh-contracts");
                if (refreshBtn) refreshBtn.click();
            }
        } else if (sectionName === "dashboard") {
            if (navDashboard) navDashboard.classList.add("active");
            if (sectionDashboard) {
                sectionDashboard.classList.add("active");
                loadDashboard();
            }
        } else if (sectionName === "admin") {
            if (navAdmin) navAdmin.classList.add("active");
            if (sectionAdmin) {
                sectionAdmin.classList.add("active");
                loadAdminPanel();
            }
        }
    }

    navUpload.addEventListener("click", () => setActiveSection("upload"));
    navChat.addEventListener("click", () => setActiveSection("chat"));
    navFiles.addEventListener("click", () => setActiveSection("files"));
    if (navContracts) navContracts.addEventListener("click", () => setActiveSection("contracts"));
    if (navDashboard) navDashboard.addEventListener("click", () => setActiveSection("dashboard"));
    if (navAdmin) navAdmin.addEventListener("click", () => setActiveSection("admin"));

    // --- Read ?section= param on load to support redirects from other pages ---
    const initParams = new URLSearchParams(window.location.search);
    const initSection = initParams.get("section");
    const initAction = initParams.get("action");
    if (initSection) {
        if (initSection === "contracts" && initAction === "signed") {
            // Coming from a sign — wait briefly for Aurora to settle before loading
            setTimeout(() => setActiveSection("contracts"), 800);
        } else {
            setActiveSection(initSection);
        }
    }

    // Logout with confirmation
    const logoutModal = document.getElementById("logout-modal");
    const logoutCancel = document.getElementById("logout-cancel");
    const logoutConfirm = document.getElementById("logout-confirm");

    btnLogout.addEventListener("click", () => {
        logoutModal.classList.remove("hidden");
    });

    logoutCancel.addEventListener("click", () => {
        logoutModal.classList.add("hidden");
    });

    logoutModal.addEventListener("click", (e) => {
        if (e.target === logoutModal) logoutModal.classList.add("hidden");
    });

    logoutConfirm.addEventListener("click", () => {
        logoutModal.classList.add("hidden");
        logout();
    });

    // --- Upload ---
    const dropZone = document.getElementById("drop-zone");
    const fileInput = document.getElementById("file-input");
    const btnBrowse = document.getElementById("btn-browse");
    const uploadStatus = document.getElementById("upload-status");

    btnBrowse.addEventListener("click", (e) => {
        e.stopPropagation();
        fileInput.click();
    });

    dropZone.addEventListener("click", () => fileInput.click());

    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("dragover");
    });

    dropZone.addEventListener("dragleave", () => {
        dropZone.classList.remove("dragover");
    });

    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("dragover");
        handleFiles(e.dataTransfer.files);
    });

    fileInput.addEventListener("change", () => {
        handleFiles(fileInput.files);
        fileInput.value = "";
    });

    async function handleFiles(files) {
        for (const file of files) {
            await uploadFile(file);
        }
    }

    async function uploadFile(file) {
        const MAX_FILE_SIZE = 10 * 1024 * 1024;

        if (file.size > MAX_FILE_SIZE) {
            addStatus(`${file.name}: exceeds 10 MB limit (${(file.size / 1024 / 1024).toFixed(1)} MB)`, "error");
            return;
        }

        const statusEl = addStatus(`Uploading ${file.name}...`, "uploading");

        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/upload`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Authorization: getIdToken(),
                },
                body: JSON.stringify({ filename: file.name }),
            });

            if (!res.ok) throw new Error("Failed to get upload URL");

            const { upload_url } = await res.json();

            const uploadRes = await fetch(upload_url, {
                method: "PUT",
                headers: { "Content-Type": "application/octet-stream" },
                body: file,
            });

            if (!uploadRes.ok) throw new Error("Upload to S3 failed");

            statusEl.textContent = `${file.name} uploaded successfully`;
            statusEl.className = "status-item success";
            showToast(`${file.name} uploaded`, "success");
        } catch (err) {
            statusEl.textContent = `${file.name}: ${err.message}`;
            statusEl.className = "status-item error";
        }
    }

    function addStatus(text, type) {
        const el = document.createElement("div");
        el.className = `status-item ${type}`;
        el.textContent = text;
        uploadStatus.prepend(el);
        return el;
    }

    // --- Chat (Multi-Session) ---
    const chatMessages = document.getElementById("chat-messages");
    const chatInput = document.getElementById("chat-input");
    const btnSend = document.getElementById("btn-send");
    const btnNewSession = document.getElementById("btn-new-session");
    const sessionsList = document.getElementById("sessions-list");

    // Session state - stored in sessionStorage (cleared on logout)
    let sessions = JSON.parse(sessionStorage.getItem("chat_sessions") || "[]");
    let activeSessionId = sessionStorage.getItem("active_session_id") || null;

    // Initialize: create first session if none exist
    if (sessions.length === 0) {
        createNewSession();
    } else {
        if (!activeSessionId || !sessions.find(s => s.id === activeSessionId)) {
            activeSessionId = sessions[0].id;
        }
        renderSessions();
        renderChatMessages();
    }

    btnNewSession.addEventListener("click", () => {
        createNewSession();
    });

    // Export chat as PDF
    const btnExportChat = document.getElementById("btn-export-chat");
    btnExportChat.addEventListener("click", () => {
        exportChatAsPdf();
    });

    function exportChatAsPdf() {
        const session = getActiveSession();
        if (!session || session.messages.length <= 1) {
            showToast("No messages to export", "info");
            return;
        }

        // Build plain text content for PDF
        const lines = [];
        lines.push("Judy.ai - Chat Export");
        lines.push(`Session: ${session.name}`);
        lines.push(`Exported: ${new Date().toLocaleString()}`);
        lines.push("─".repeat(60));
        lines.push("");

        for (const msg of session.messages) {
            const role = msg.role === "user" ? "You" : msg.role === "assistant" ? "Judy" : "System";
            lines.push(`[${role}]`);
            // Word wrap long lines at ~80 chars
            const wrapped = wrapText(msg.text, 80);
            lines.push(...wrapped);
            lines.push("");
        }

        // Generate PDF using minimal PDF spec (no external library)
        const pdfContent = generateMinimalPdf(lines);
        const blob = new Blob([pdfContent], { type: "application/pdf" });
        const url = URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = `judy-chat-${session.name.replace(/\s+/g, "-").toLowerCase()}.pdf`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        showToast("Chat exported as PDF", "success");
    }

    function wrapText(text, maxChars) {
        const result = [];
        const paragraphs = text.split("\n");
        for (const para of paragraphs) {
            if (para.length <= maxChars) {
                result.push(para);
            } else {
                const words = para.split(" ");
                let line = "";
                for (const word of words) {
                    if ((line + " " + word).trim().length > maxChars) {
                        result.push(line.trim());
                        line = word;
                    } else {
                        line = line ? line + " " + word : word;
                    }
                }
                if (line.trim()) result.push(line.trim());
            }
        }
        return result;
    }

    function generateMinimalPdf(lines) {
        // Minimal valid PDF with text content
        const fontSize = 10;
        const lineHeight = 14;
        const marginLeft = 50;
        const pageHeight = 792;
        const pageWidth = 612;
        const marginTop = 50;
        const marginBottom = 50;
        const usableHeight = pageHeight - marginTop - marginBottom;
        const linesPerPage = Math.floor(usableHeight / lineHeight);

        // Split lines into pages
        const pages = [];
        for (let i = 0; i < lines.length; i += linesPerPage) {
            pages.push(lines.slice(i, i + linesPerPage));
        }

        // PDF objects
        const objects = [];
        let objNum = 0;

        function addObj(content) {
            objNum++;
            objects.push({ num: objNum, content });
            return objNum;
        }

        // Object 1: Catalog
        const catalogNum = addObj("<< /Type /Catalog /Pages 2 0 R >>");

        // Object 2: Pages (placeholder - will update)
        const pagesNum = addObj("");

        // Create page objects
        const pageObjNums = [];

        for (const page of pages) {
            // Build stream content - use absolute positioning per line
            let stream = `BT\n/F1 ${fontSize} Tf\n`;
            let y = pageHeight - marginTop;
            for (const line of page) {
                const escaped = line
                    .replace(/\\/g, "\\\\")
                    .replace(/\(/g, "\\(")
                    .replace(/\)/g, "\\)")
                    .replace(/[^\x20-\x7E]/g, " ");
                // Use absolute position with Tm (text matrix) for each line
                stream += `1 0 0 1 ${marginLeft} ${y} Tm (${escaped}) Tj\n`;
                y -= lineHeight;
            }
            stream += "ET";

            const streamNum = addObj(
                `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`
            );

            const pageNum = addObj(
                `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth} ${pageHeight}] ` +
                `/Contents ${streamNum} 0 R ` +
                `/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Courier >> >> >> >>`
            );
            pageObjNums.push(pageNum);
        }

        // Update pages object
        const kidsStr = pageObjNums.map(n => `${n} 0 R`).join(" ");
        objects[pagesNum - 1].content = `<< /Type /Pages /Kids [${kidsStr}] /Count ${pages.length} >>`;

        // Build PDF file
        let pdf = "%PDF-1.4\n";
        const offsets = [];

        for (const obj of objects) {
            offsets.push(pdf.length);
            pdf += `${obj.num} 0 obj\n${obj.content}\nendobj\n`;
        }

        // Cross-reference table
        const xrefOffset = pdf.length;
        pdf += "xref\n";
        pdf += `0 ${objects.length + 1}\n`;
        pdf += "0000000000 65535 f \n";
        for (const offset of offsets) {
            pdf += String(offset).padStart(10, "0") + " 00000 n \n";
        }

        pdf += "trailer\n";
        pdf += `<< /Size ${objects.length + 1} /Root ${catalogNum} 0 R >>\n`;
        pdf += "startxref\n";
        pdf += `${xrefOffset}\n`;
        pdf += "%%EOF\n";

        // Convert to binary
        const bytes = new Uint8Array(pdf.length);
        for (let i = 0; i < pdf.length; i++) {
            bytes[i] = pdf.charCodeAt(i);
        }
        return bytes;
    }

    function createNewSession() {
        const sessionId = "s_" + Date.now();
        const sessionNum = sessions.length + 1;
        const session = {
            id: sessionId,
            name: `Chat ${sessionNum}`,
            messages: [{ role: "assistant", text: "Hello! I'm Judy. Ask me anything about your uploaded documents." }],
            createdAt: new Date().toISOString(),
        };
        sessions.unshift(session);
        activeSessionId = sessionId;
        saveSessions();
        renderSessions();
        renderChatMessages();
    }

    function switchSession(sessionId) {
        activeSessionId = sessionId;
        sessionStorage.setItem("active_session_id", sessionId);
        renderSessions();
        renderChatMessages();
        chatInput.focus();
    }

    function getActiveSession() {
        return sessions.find(s => s.id === activeSessionId);
    }

    function saveSessions() {
        sessionStorage.setItem("chat_sessions", JSON.stringify(sessions));
        sessionStorage.setItem("active_session_id", activeSessionId);
    }

    function renderSessions() {
        sessionsList.innerHTML = sessions.map(s => {
            const isActive = s.id === activeSessionId;
            const preview = s.messages.length > 1
                ? s.messages.find(m => m.role === "user")?.text || s.name
                : s.name;
            return `<div class="session-item ${isActive ? "active" : ""}" data-session-id="${s.id}" title="${escapeHtml(preview)}">${escapeHtml(preview)}</div>`;
        }).join("");

        // Click handlers
        sessionsList.querySelectorAll(".session-item").forEach(el => {
            el.addEventListener("click", () => {
                switchSession(el.dataset.sessionId);
            });
        });
    }

    function renderChatMessages() {
        const session = getActiveSession();
        if (!session) return;

        const logoSvg = `<svg class="avatar-logo"><use href="#judy-logo"/></svg>`;

        chatMessages.innerHTML = session.messages.map(msg => {
            const renderedContent = msg.role === "user"
                ? escapeHtml(msg.text)
                : renderMarkdown(msg.text);

            if (msg.role === "assistant" || msg.role === "error") {
                return `<div class="message ${msg.role}">
                    <div class="message-avatar assistant-avatar">${logoSvg}</div>
                    <div class="message-bubble">
                        <div class="message-content">${renderedContent}</div>
                    </div>
                </div>`;
            } else {
                return `<div class="message user">
                    <div class="message-avatar user-avatar">U</div>
                    <div class="message-bubble"><div class="message-content">${renderedContent}</div></div>
                </div>`;
            }
        }).join("");

        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function renderMarkdown(text) {
        if (!text) return "";
        let html = escapeHtml(text);

        // Render markdown tables
        html = html.replace(/((?:\|.*\|[\r\n]+)+)/g, (match) => {
            const rows = match.trim().split("\n").filter(r => r.trim());
            if (rows.length < 2) return match;

            // Check if second row is separator (|---|---|)
            const isSeparator = (row) => /^\|[\s\-:|]+\|$/.test(row.trim());
            const hasSeparator = rows.length > 1 && isSeparator(rows[1]);

            let tableHtml = '<table class="chat-table">';
            rows.forEach((row, idx) => {
                if (hasSeparator && idx === 1) return; // Skip separator row
                const cells = row.split("|").filter((c, i, arr) => i > 0 && i < arr.length - 1);
                const tag = (hasSeparator && idx === 0) ? "th" : "td";
                tableHtml += "<tr>" + cells.map(c => `<${tag}>${c.trim()}</${tag}>`).join("") + "</tr>";
            });
            tableHtml += "</table>";
            return tableHtml;
        });

        // Bold: **text**
        html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");

        // Bullet lists: lines starting with - or *
        html = html.replace(/^[\-\*] (.+)$/gm, "<li>$1</li>");
        html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>");

        // Line breaks
        html = html.replace(/\n/g, "<br>");

        return html;
    }

    btnSend.addEventListener("click", sendMessage);
    chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    async function sendMessage() {
        const query = chatInput.value.trim();
        if (!query) return;

        const session = getActiveSession();
        if (!session) return;

        // Add user message
        session.messages.push({ role: "user", text: query });
        saveSessions();
        renderChatMessages();
        renderSessions(); // Update preview
        chatInput.value = "";
        btnSend.disabled = true;

        // Show typing indicator
        const typingEl = showTypingIndicator();

        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/chat`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Authorization: getIdToken(),
                },
                body: JSON.stringify({ query }),
            });

            if (!res.ok) throw new Error("Request failed");

            const data = await res.json();
            removeTypingIndicator(typingEl);

            const answer = data.answer || "No relevant information found.";
            session.messages.push({ role: "assistant", text: answer });
            saveSessions();
            renderChatMessages();
        } catch (err) {
            removeTypingIndicator(typingEl);
            session.messages.push({ role: "error", text: `Error: ${err.message}` });
            saveSessions();
            renderChatMessages();
        } finally {
            btnSend.disabled = false;
            chatInput.focus();
        }
    }

    function showTypingIndicator() {
        const el = document.createElement("div");
        el.className = "message assistant";
        el.id = "typing-indicator";
        el.innerHTML = `
            <div class="message-avatar assistant-avatar">
                <svg class="avatar-logo"><use href="#judy-logo"/></svg>
            </div>
            <div class="message-bubble">
                <div class="typing-indicator">
                    <span></span><span></span><span></span>
                </div>
            </div>
        `;
        chatMessages.appendChild(el);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return el;
    }

    function removeTypingIndicator(el) {
        if (el && el.parentNode) {
            el.parentNode.removeChild(el);
        }
    }

    // --- Files Section ---
    const filesList = document.getElementById("files-list");
    const btnUploadFile = document.getElementById("btn-upload-file");
    const contextMenu = document.getElementById("context-menu");
    const ctxDelete = document.getElementById("ctx-delete");
    const deleteModal = document.getElementById("delete-modal");
    const deleteModalFilename = document.getElementById("delete-modal-filename");
    const modalConfirm = document.getElementById("modal-confirm");
    const modalCancel = document.getElementById("modal-cancel");

    let selectedDocumentId = null;
    let selectedDocumentName = null;

    btnUploadFile.addEventListener("click", () => setActiveSection("upload"));

    // Refresh button
    const btnRefresh = document.getElementById("btn-refresh-files");
    btnRefresh.addEventListener("click", () => {
        btnRefresh.disabled = true;
        loadFiles().then(() => {
            btnRefresh.disabled = false;
        });
    });

    async function loadFiles() {
        filesList.innerHTML = '<div class="files-loading"><div class="loading-spinner"></div><span>Loading files...</span></div>';

        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/files`, {
                headers: { Authorization: getIdToken() },
            });

            if (!res.ok) throw new Error("Failed to load files");

            const data = await res.json();
            renderFilesList(data.files);
        } catch (err) {
            filesList.innerHTML = `<div class="files-error">Error: ${err.message}</div>`;
        }
    }

    function renderFilesList(files) {
        if (!files || files.length === 0) {
            filesList.innerHTML = `
                <div class="files-empty">
                    <svg class="files-empty-logo"><use href="#judy-logo"/></svg>
                    <span>No files uploaded yet</span>
                    <span style="font-size: 0.8rem">Click "Upload File" to add documents</span>
                </div>
            `;
            return;
        }

        const fileIcon = `<svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>`;

        filesList.innerHTML = `
            <div class="files-table">
                <div class="files-table-header">
                    <span class="col-name">File Name</span>
                    <span class="col-status">KB Status</span>
                    <span class="col-size">Size</span>
                    <span class="col-uploader">Uploaded By</span>
                    <span class="col-date">Upload Date</span>
                </div>
                ${files.map(f => `
                    <div class="file-row" data-id="${f.document_id}" data-name="${escapeHtml(f.document_name)}">
                        <span class="col-name" title="${escapeHtml(f.document_name)}">${fileIcon}${escapeHtml(f.document_name)}</span>
                        <span class="col-status">${renderKbStatus(f.kb_status)}</span>
                        <span class="col-size">${formatFileSize(f.file_size_kb)}</span>
                        <span class="col-uploader" title="${escapeHtml(f.uploaded_by)}">${escapeHtml(f.uploaded_by)}</span>
                        <span class="col-date">${formatDate(f.upload_date)}</span>
                    </div>
                `).join("")}
            </div>
        `;
    }

    // --- Context Menu ---
    filesList.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const fileRow = e.target.closest(".file-row");
        if (!fileRow) {
            hideContextMenu();
            return;
        }

        selectedDocumentId = fileRow.dataset.id;
        selectedDocumentName = fileRow.dataset.name;

        contextMenu.style.left = `${e.pageX}px`;
        contextMenu.style.top = `${e.pageY}px`;
        contextMenu.classList.remove("hidden");
    });

    document.addEventListener("click", (e) => {
        if (!contextMenu.contains(e.target)) {
            hideContextMenu();
        }
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            hideContextMenu();
            hideDeleteModal();
        }
    });

    function hideContextMenu() {
        contextMenu.classList.add("hidden");
    }

    ctxDelete.addEventListener("click", () => {
        hideContextMenu();
        showDeleteModal();
    });

    // --- Delete Modal ---
    function showDeleteModal() {
        deleteModalFilename.textContent = selectedDocumentName;
        deleteModal.classList.remove("hidden");
    }

    function hideDeleteModal() {
        deleteModal.classList.add("hidden");
        selectedDocumentId = null;
        selectedDocumentName = null;
    }

    modalCancel.addEventListener("click", hideDeleteModal);

    deleteModal.addEventListener("click", (e) => {
        if (e.target === deleteModal) hideDeleteModal();
    });

    modalConfirm.addEventListener("click", async () => {
        if (!selectedDocumentId) return;

        const docId = selectedDocumentId;
        const docName = selectedDocumentName;
        hideDeleteModal();

        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/files/${docId}`, {
                method: "DELETE",
                headers: { Authorization: getIdToken() },
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.error || "Delete failed");
            }

            showToast(`${docName} deleted`, "success");
            loadFiles();
        } catch (err) {
            showToast(`Failed to delete: ${err.message}`, "error");
        }
    });

    // --- Toast Notifications ---
    function showToast(message, type = "info") {
        const container = document.getElementById("toast-container");
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

    // --- Utility Functions ---
    function formatDate(isoString) {
        if (!isoString) return "-";
        const date = new Date(isoString);
        return date.toLocaleDateString("en-US", {
            year: "numeric",
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    function formatFileSize(kb) {
        if (!kb || kb === 0) return "-";
        if (kb < 1024) return `${kb.toFixed(1)} KB`;
        return `${(kb / 1024).toFixed(1)} MB`;
    }

    function renderKbStatus(status) {
        if (status === "ingested") {
            return '<span class="kb-badge kb-ingested">Ingested</span>';
        } else if (status === "failed") {
            return '<span class="kb-badge kb-failed">Failed</span>';
        } else if (status === "deleting") {
            return '<span class="kb-badge kb-deleting"><span class="kb-spinner"></span>Deleting</span>';
        } else {
            return '<span class="kb-badge kb-loading"><span class="kb-spinner"></span>Loading</span>';
        }
    }

    function escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    // =============================================================
    // Dashboard
    // =============================================================
    async function loadDashboard() {
        // Fetch all contracts first
        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/files`, {
                headers: { Authorization: getIdToken() },
            });
            if (!res.ok) throw new Error("Failed to load contracts");
            const data = await res.json();
            const files = data.files || [];

            // Pending review — uploaded / analyzing / ready_for_review
            const pending = files.filter(f =>
                ["uploaded", "analyzing", "ready_for_review"].includes(f.contract_status || "uploaded")
                && f.contract_id
            );

            // Signed contracts — for obligations
            const signed = files.filter(f => f.contract_status === "signed" && f.contract_id);

            // Contracts with analysis — for risks
            const withAnalysis = files.filter(f => f.contract_id);

            renderDashboardPending(pending);
            loadDashboardRisks(withAnalysis);
            loadDashboardObligations(signed);

        } catch (err) {
            ["dash-pending-list", "dash-risks-list", "dash-obligations-list"].forEach(id => {
                document.getElementById(id).innerHTML = `<div class="dashboard-empty">Failed to load: ${err.message}</div>`;
            });
        }
    }

    function renderDashboardPending(contracts) {
        const list = document.getElementById("dash-pending-list");
        const countEl = document.getElementById("dash-pending-count");

        if (!contracts.length) {
            list.innerHTML = `<div class="dashboard-empty">
                <span class="dashboard-empty-icon">✅</span>
                <span>No contracts pending review</span>
            </div>`;
            return;
        }

        countEl.textContent = contracts.length;
        countEl.classList.remove("hidden");

        list.innerHTML = contracts.map(f => {
            const status = f.contract_status || "uploaded";
            return `<div class="dashboard-item dashboard-item-clickable"
                        onclick="window.location.href='review.html?contractId=${f.contract_id}&documentId=${f.document_id}&name=${encodeURIComponent(f.document_name)}&status=${status}'">
                <div class="dashboard-item-left">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="opacity:0.5;flex-shrink:0">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                    </svg>
                    <span class="dashboard-item-name">${escapeHtml(f.document_name)}</span>
                </div>
                <span class="contract-status ${status}" style="font-size:0.65rem;padding:2px 8px">${status.replace(/_/g, " ")}</span>
            </div>`;
        }).join("");
    }

    async function loadDashboardRisks(contracts) {
        const list = document.getElementById("dash-risks-list");
        const countEl = document.getElementById("dash-risks-count");

        if (!contracts.length) {
            list.innerHTML = `<div class="dashboard-empty"><span>No contracts to analyse</span></div>`;
            return;
        }

        try {
            // Fetch risk status for all contracts in parallel (use status endpoint first)
            const statusResults = await Promise.all(
                contracts.map(f =>
                    fetch(`${CONFIG.API_BASE_URL}/contracts/${f.contract_id}/analysis`, {
                        headers: { Authorization: getIdToken() },
                    }).then(r => r.ok ? r.json() : null).catch(() => null)
                )
            );

            // Find contracts where risk agent succeeded and has results
            const contractsWithRisks = contracts
                .map((f, i) => ({ file: f, status: statusResults[i] }))
                .filter(({ status }) =>
                    status?.agents?.riskClause?.status === "succeeded" &&
                    status?.agents?.riskClause?.resultCount > 0
                );

            if (!contractsWithRisks.length) {
                list.innerHTML = `<div class="dashboard-empty">
                    <span class="dashboard-empty-icon">✅</span>
                    <span>No risks flagged across contracts</span>
                </div>`;
                return;
            }

            // Fetch risk details for contracts that have risks (limit to 5 contracts)
            const top = contractsWithRisks.slice(0, 5);
            const riskResults = await Promise.all(
                top.map(({ file }) =>
                    fetch(`${CONFIG.API_BASE_URL}/contracts/${file.contract_id}/analysis/risk-clause`, {
                        headers: { Authorization: getIdToken() },
                    }).then(r => r.ok ? r.json() : null).catch(() => null)
                )
            );

            // Flatten all risks, tag with contract name, keep high+medium, limit 10
            const allRisks = [];
            riskResults.forEach((data, i) => {
                if (!data?.risks) return;
                data.risks.forEach(r => {
                    if (r.severity === "high" || r.severity === "medium") {
                        allRisks.push({ ...r, contractName: top[i].file.document_name, contractId: top[i].file.contract_id, contractStatus: top[i].file.contract_status || "uploaded" });
                    }
                });
            });

            // Sort high first, then medium
            allRisks.sort((a, b) => (a.severity === "high" ? -1 : 1));
            const displayRisks = allRisks.slice(0, 10);

            countEl.textContent = allRisks.length;
            countEl.classList.remove("hidden");

            list.innerHTML = displayRisks.map(r => `
                <div class="dashboard-item dashboard-item-clickable"
                     onclick="window.location.href='review.html?contractId=${r.contractId}&name=${encodeURIComponent(r.contractName)}&status=${r.contractStatus}'">
                    <div class="dashboard-item-left" style="flex-direction:column;align-items:flex-start;gap:3px">
                        <div style="display:flex;align-items:center;gap:6px">
                            <span class="risk-severity risk-severity-${r.severity}" style="font-size:0.62rem;padding:1px 7px">${r.severity}</span>
                            <span class="dashboard-item-name">${escapeHtml(r.title)}</span>
                        </div>
                        <span style="font-size:0.72rem;color:var(--text-muted)">${escapeHtml(r.contractName)}</span>
                    </div>
                </div>`).join("");

        } catch (err) {
            list.innerHTML = `<div class="dashboard-empty">Failed to load risks: ${err.message}</div>`;
        }
    }

    async function loadDashboardObligations(signedContracts) {
        const list = document.getElementById("dash-obligations-list");
        const countEl = document.getElementById("dash-obligations-count");

        if (!signedContracts.length) {
            list.innerHTML = `<div class="dashboard-empty">
                <span class="dashboard-empty-icon">📋</span>
                <span>No obligations tracked yet</span>
                <span style="font-size:0.72rem;color:var(--text-muted)">Obligations appear after contracts are signed</span>
            </div>`;
            return;
        }

        try {
            const results = await Promise.all(
                signedContracts.map(f =>
                    fetch(`${CONFIG.API_BASE_URL}/contracts/${f.contract_id}/analysis/obligation-tracking`, {
                        headers: { Authorization: getIdToken() },
                    }).then(r => r.ok ? r.json() : null).catch(() => null)
                )
            );

            // Flatten obligations, tag with contract name
            const allObligations = [];
            results.forEach((data, i) => {
                if (!data?.obligations) return;
                data.obligations.forEach(o => {
                    allObligations.push({ ...o, contractName: signedContracts[i].document_name, contractId: signedContracts[i].contract_id });
                });
            });

            if (!allObligations.length) {
                list.innerHTML = `<div class="dashboard-empty">
                    <span class="dashboard-empty-icon">📋</span>
                    <span>No obligations found</span>
                </div>`;
                return;
            }

            // Sort by due date (nulls last)
            allObligations.sort((a, b) => {
                if (!a.dueDate && !b.dueDate) return 0;
                if (!a.dueDate) return 1;
                if (!b.dueDate) return -1;
                return new Date(a.dueDate) - new Date(b.dueDate);
            });

            countEl.textContent = allObligations.length;
            countEl.classList.remove("hidden");

            list.innerHTML = allObligations.map(o => `
                <div class="dashboard-item">
                    <div class="dashboard-item-left" style="flex-direction:column;align-items:flex-start;gap:3px">
                        <div style="display:flex;align-items:center;gap:6px">
                            <span class="obligation-type-badge" style="font-size:0.62rem;padding:1px 7px">${escapeHtml(o.obligationType || "")}</span>
                            <span class="dashboard-item-name">${escapeHtml(o.description || "")}</span>
                        </div>
                        <div style="display:flex;gap:10px;align-items:center">
                            <span style="font-size:0.72rem;color:var(--text-muted)">${escapeHtml(o.contractName)}</span>
                            ${o.dueDate
                                ? `<span style="font-size:0.72rem;color:#ffa726;font-weight:600">Due: ${escapeHtml(o.dueDate)}</span>`
                                : o.rawDateText
                                    ? `<span style="font-size:0.72rem;color:var(--text-muted);font-style:italic">${escapeHtml(o.rawDateText)}</span>`
                                    : ""}
                        </div>
                    </div>
                </div>`).join("");

        } catch (err) {
            list.innerHTML = `<div class="dashboard-empty">Failed to load obligations: ${err.message}</div>`;
        }
    }

}); // end DOMContentLoaded


// =============================================================
// Admin Panel
// =============================================================
function loadAdminPanel() {
    loadAdminTemplates();
    loadAdminSettings();
}

async function loadAdminTemplates() {
    const container = document.getElementById("admin-templates-list");
    if (!container) return;
    container.innerHTML = `<div class="files-loading"><div class="loading-spinner"></div><span>Loading...</span></div>`;

    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/admin/templates`, {
            headers: { Authorization: getIdToken() },
        });
        if (!res.ok) throw new Error(`Request failed (${res.status})`);
        const data = await res.json();
        renderAdminTemplates(data.templates || []);
    } catch (err) {
        container.innerHTML = `<div class="analysis-empty analysis-error">Failed to load templates: ${err.message}</div>`;
    }
}

function renderAdminTemplates(templates) {
    const container = document.getElementById("admin-templates-list");
    if (!templates.length) {
        container.innerHTML = `<div class="analysis-empty">No templates found.</div>`;
        return;
    }

    container.innerHTML = templates.map(t => `
        <div class="admin-template-row" id="template-row-${t.id}">
            <div class="admin-template-info">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
                    <input type="text" class="input-field admin-template-name" value="${escapeHtml(t.name)}" data-id="${t.id}" style="width:220px;font-weight:600">
                    ${t.isDefault ? `<span class="contract-status signed" style="font-size:0.65rem;padding:2px 8px">Default</span>` : `<button class="btn-secondary btn-sm btn-set-default" data-id="${t.id}" style="font-size:0.72rem">Set as Default</button>`}
                </div>
                <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
                    <div>
                        <label style="font-size:0.72rem;color:var(--text-muted)">Routing Type</label>
                        <select class="input-field admin-template-routing" data-id="${t.id}" style="padding:6px 10px;font-size:0.82rem">
                            <option value="sequential" ${t.routingType === "sequential" ? "selected" : ""}>Sequential</option>
                            <option value="parallel" ${t.routingType === "parallel" ? "selected" : ""}>Parallel</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:0.72rem;color:var(--text-muted)">Signer Roles (comma-separated)</label>
                        <input type="text" class="input-field admin-template-roles" value="${escapeHtml((t.signerRoles || []).join(", "))}" data-id="${t.id}" style="width:260px;font-size:0.82rem" placeholder="e.g. supplier, company">
                    </div>
                    <button class="btn-primary btn-sm btn-save-template" data-id="${t.id}" style="margin-top:18px">Save</button>
                </div>
            </div>
        </div>
    `).join("");

    // Set default buttons
    container.querySelectorAll(".btn-set-default").forEach(btn => {
        btn.addEventListener("click", () => saveTemplate(btn.dataset.id, { isDefault: true }));
    });

    // Save buttons
    container.querySelectorAll(".btn-save-template").forEach(btn => {
        btn.addEventListener("click", () => {
            const id = btn.dataset.id;
            const name = container.querySelector(`.admin-template-name[data-id="${id}"]`).value.trim();
            const routingType = container.querySelector(`.admin-template-routing[data-id="${id}"]`).value;
            const rolesRaw = container.querySelector(`.admin-template-roles[data-id="${id}"]`).value;
            const signerRoles = rolesRaw.split(",").map(r => r.trim()).filter(Boolean);

            if (!name) { showAdminToast("Template name is required", "error"); return; }
            if (!signerRoles.length) { showAdminToast("At least one signer role is required", "error"); return; }

            saveTemplate(id, { name, routingType, signerRoles });
        });
    });
}

async function saveTemplate(templateId, updates) {
    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/admin/templates/${templateId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", Authorization: getIdToken() },
            body: JSON.stringify(updates),
        });
        if (!res.ok) throw new Error(`Failed to save (${res.status})`);
        showAdminToast("Template saved", "success");
        loadAdminTemplates(); // refresh
    } catch (err) {
        showAdminToast(`Error: ${err.message}`, "error");
    }
}

async function loadAdminSettings() {
    const container = document.getElementById("admin-settings-form");
    if (!container) return;
    container.innerHTML = `<div class="files-loading"><div class="loading-spinner"></div><span>Loading...</span></div>`;

    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/admin/settings`, {
            headers: { Authorization: getIdToken() },
        });
        if (!res.ok) throw new Error(`Request failed (${res.status})`);
        const data = await res.json();
        renderAdminSettings(data.settings || {});
    } catch (err) {
        container.innerHTML = `<div class="analysis-empty analysis-error">Failed to load settings: ${err.message}</div>`;
    }
}

function renderAdminSettings(settings) {
    const container = document.getElementById("admin-settings-form");

    container.innerHTML = `
        <div class="admin-settings-grid">
            <div class="admin-setting-row">
                <label>Application Name</label>
                <input type="text" id="setting-app-name" class="input-field" value="${escapeHtml(settings.app_name || "Judy.ai")}" style="max-width:280px">
            </div>
            <div class="admin-setting-row">
                <label>Max File Size (MB)</label>
                <input type="number" id="setting-max-file-size" class="input-field" value="${settings.max_file_size_mb || 10}" min="1" max="100" style="max-width:100px">
            </div>
            <div class="admin-setting-row">
                <label>Email Notifications</label>
                <div style="display:flex;flex-direction:column;gap:8px;margin-top:4px">
                    <label class="admin-toggle-label">
                        <input type="checkbox" id="setting-notify-assigned" ${settings.notify_document_assigned === "true" ? "checked" : ""}>
                        <span>Document assigned for signature</span>
                    </label>
                    <label class="admin-toggle-label">
                        <input type="checkbox" id="setting-notify-completed" ${settings.notify_signature_completed === "true" ? "checked" : ""}>
                        <span>Signature completed</span>
                    </label>
                    <label class="admin-toggle-label">
                        <input type="checkbox" id="setting-notify-risk" ${settings.notify_risk_flagged === "true" ? "checked" : ""}>
                        <span>Risk flagged by AI</span>
                    </label>
                </div>
            </div>
        </div>
        <div style="margin-top:16px">
            <button id="btn-save-settings" class="btn-primary">Save Settings</button>
        </div>`;

    document.getElementById("btn-save-settings").addEventListener("click", async () => {
        const btn = document.getElementById("btn-save-settings");
        btn.disabled = true;
        btn.textContent = "Saving...";

        const updated = {
            app_name: document.getElementById("setting-app-name").value.trim() || "Judy.ai",
            max_file_size_mb: String(document.getElementById("setting-max-file-size").value || "10"),
            notify_document_assigned: String(document.getElementById("setting-notify-assigned").checked),
            notify_signature_completed: String(document.getElementById("setting-notify-completed").checked),
            notify_risk_flagged: String(document.getElementById("setting-notify-risk").checked),
        };

        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/admin/settings`, {
                method: "PUT",
                headers: { "Content-Type": "application/json", Authorization: getIdToken() },
                body: JSON.stringify({ settings: updated }),
            });
            if (!res.ok) throw new Error(`Failed to save (${res.status})`);
            showAdminToast("Settings saved", "success");
        } catch (err) {
            showAdminToast(`Error: ${err.message}`, "error");
        } finally {
            btn.disabled = false;
            btn.textContent = "Save Settings";
        }
    });
}

function showAdminToast(message, type = "info") {
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

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}


// =============================================================
// Contracts Section — Signature Workflow
// =============================================================

(function () {
    // Utility functions (local to this module)
    function escapeHtml(text) {
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

    function formatDate(iso) {
        if (!iso) return "";
        return new Date(iso).toLocaleDateString("en-US", {
            year: "numeric", month: "short", day: "numeric"
        });
    }

    // Wait for DOM to be ready
    document.addEventListener("DOMContentLoaded", () => {
        if (!isAuthenticated()) return;

        const navContracts = document.getElementById("nav-contracts");
        const sectionContracts = document.getElementById("section-contracts");

        if (!navContracts) return;

        document.getElementById("btn-refresh-contracts").addEventListener("click", loadContracts);

        // --- State ---
        let selectedContractId = null;
        let selectedContractName = null;
        let batchSelectedIds = new Set();
        let batchSignMode = false;

        // --- Batch Sign Button ---
        const btnSignSelected = document.getElementById("btn-sign-selected");
        btnSignSelected.addEventListener("click", () => openBatchSignatureModal());

        function updateBatchSignButton() {
            const count = batchSelectedIds.size;
            if (count > 0) {
                btnSignSelected.classList.remove("hidden");
                document.getElementById("btn-sign-selected-label").textContent = `Sign Selected (${count})`;
            } else {
                btnSignSelected.classList.add("hidden");
            }
        }

        // =============================================================
        // Load Contracts (from DynamoDB via GET /files, mapped to contracts)
        // =============================================================
        async function loadContracts() {
            const list = document.getElementById("contracts-list");
            list.innerHTML = `<div class="files-loading"><div class="loading-spinner"></div><span>Loading contracts...</span></div>`;
            console.log("loadContracts called");

            try {
                const res = await fetch(`${CONFIG.API_BASE_URL}/files`, {
                    headers: { Authorization: getIdToken() },
                });
                console.log("API response status:", res.status);
                if (!res.ok) throw new Error("Failed to load contracts");
                const data = await res.json();
                console.log("Files data:", data);
                renderContracts(data.files || []);
            } catch (err) {
                console.error("loadContracts error:", err);
                list.innerHTML = `<div class="files-empty">Failed to load contracts: ${err.message}</div>`;
            }
        }

        function renderContracts(files) {
            const list = document.getElementById("contracts-list");
            console.log("renderContracts called, list element:", list, "files count:", files.length);
            if (!list) {
                console.error("contracts-list element not found!");
                return;
            }

            // Reset batch state on re-render
            batchSelectedIds.clear();
            updateBatchSignButton();

            if (!files.length) {
                list.innerHTML = `
                    <div class="files-empty">
                        <svg class="files-empty-logo"><use href="#judy-logo"/></svg>
                        <span>No contracts uploaded yet</span>
                        <span style="font-size: 0.8rem">Upload a document to get started</span>
                    </div>`;
                return;
            }

            const fileIcon = `<svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`;

            list.innerHTML = `
                <div class="files-table">
                    <div class="files-table-header">
                        <span class="col-check"></span>
                        <span class="col-name">File Name</span>
                        <span class="col-status">Status</span>
                        <span class="col-uploader">Uploaded By</span>
                        <span class="col-date">Upload Date</span>
                        <span class="col-actions">Actions</span>
                    </div>
                    ${files.map(f => {
                        const status = f.contract_status || "uploaded";
                        const contractId = f.contract_id || "";
                        const hasContractId = !!contractId;
                        const isBatchable = hasContractId && status === "routed_for_signature";
                        return `
                        <div class="file-row${isBatchable ? " batchable" : ""}" data-contract-id="${contractId}" data-name="${escapeHtml(f.document_name)}" data-status="${status}">
                            <span class="col-check">
                                ${isBatchable ? `<label class="batch-checkbox-label" title="Select for batch signing">
                                    <input type="checkbox" class="batch-checkbox" data-contract-id="${contractId}" data-filename="${escapeHtml(f.document_name)}">
                                    <span class="batch-checkbox-custom"></span>
                                </label>` : ""}
                            </span>
                            <span class="col-name" title="${escapeHtml(f.document_name)}">
                                <span class="col-name-inner">
                                    ${fileIcon}
                                    <span class="col-name-text">
                                        <span class="col-name-label contract-name-link" data-contract-id="${contractId}" data-document-id="${f.document_id}" data-name="${encodeURIComponent(f.document_name)}" data-status="${status}" style="${hasContractId ? 'cursor:pointer' : ''}">${escapeHtml(f.document_name)}</span>
                                        ${hasContractId ? `<span class="analysis-status-line" id="analysis-status-${contractId}"><span class="analysis-status-loading">Loading analysis...</span></span>` : ""}
                                    </span>
                                </span>
                            </span>
                            <span class="col-status"><span class="contract-status ${status}">${status.replace(/_/g, " ")}</span></span>
                            <span class="col-uploader" title="${escapeHtml(f.uploaded_by)}">${escapeHtml(f.uploaded_by)}</span>
                            <span class="col-date">${formatDate(f.upload_date)}</span>
                            <span class="col-actions" id="actions-${contractId}">
                                ${!hasContractId
                                    ? `<span style="font-size:11px;color:#aaa;font-style:italic;">Re-upload to enable signing</span>`
                                    : status === "uploaded" || status === "analyzing" || status === "ready_for_review"
                                        ? `<span class="risk-badge-placeholder" id="risk-badge-${contractId}"></span>
                                           <button class="btn-primary btn-sm btn-route" data-contract-id="${contractId}" data-filename="${escapeHtml(f.document_name)}">Send for Signature</button>
                                           <button class="btn-secondary btn-sm btn-view-analysis" data-contract-id="${contractId}" data-filename="${escapeHtml(f.document_name)}" data-status="${status}">View Analysis</button>
                                           ${f.document_id ? `<button class="btn-secondary btn-sm btn-download-doc" data-document-id="${f.document_id}" data-filename="${escapeHtml(f.document_name)}" title="Download original document">Download</button>` : ""}`
                                        : status === "routed_for_signature"
                                            ? `<button class="btn-primary btn-sm btn-sign" data-contract-id="${contractId}" data-filename="${escapeHtml(f.document_name)}">Sign</button>
                                               <button class="btn-secondary btn-sm btn-signers" data-contract-id="${contractId}" data-filename="${escapeHtml(f.document_name)}">View Signers</button>
                                               <button class="btn-secondary btn-sm btn-view-analysis" data-contract-id="${contractId}" data-filename="${escapeHtml(f.document_name)}" data-status="${status}">View Analysis</button>
                                               <button class="btn-secondary btn-sm btn-cancel-routing" data-contract-id="${contractId}" data-filename="${escapeHtml(f.document_name)}" style="color:var(--color-error);border-color:rgba(239,83,80,0.3)" title="Cancel routing and re-send to correct signers">Cancel Routing</button>`
                                            : status === "signed"
                                                ? `<button class="btn-secondary btn-sm btn-signers" data-contract-id="${contractId}" data-filename="${escapeHtml(f.document_name)}">View Signers</button>
                                                   <button class="btn-secondary btn-sm btn-view-analysis" data-contract-id="${contractId}" data-filename="${escapeHtml(f.document_name)}" data-status="${status}">View Analysis</button>
                                                   ${f.document_id ? `<button class="btn-secondary btn-sm btn-download-doc" data-document-id="${f.document_id}" data-filename="${escapeHtml(f.document_name)}" title="Download original document">Download</button>` : ""}`
                                                : ``
                                }
                            </span>
                        </div>`;
                    }).join("")}
                </div>`;

            // Attach button events
            list.querySelectorAll(".btn-route").forEach(btn => {
                btn.addEventListener("click", () => openRouteModal(btn.dataset.contractId, btn.dataset.filename));
            });
            list.querySelectorAll(".btn-sign").forEach(btn => {
                btn.addEventListener("click", () => openSignatureModal(btn.dataset.contractId, btn.dataset.filename));
            });
            list.querySelectorAll(".btn-signers").forEach(btn => {
                btn.addEventListener("click", () => openSignersModal(btn.dataset.contractId, btn.dataset.filename));
            });
            list.querySelectorAll(".btn-view-analysis").forEach(btn => {
                btn.addEventListener("click", () => openAnalysisModal(btn.dataset.contractId, btn.dataset.filename, btn.dataset.status));
            });

            // Cancel Routing
            list.querySelectorAll(".btn-cancel-routing").forEach(btn => {
                btn.addEventListener("click", () => confirmCancelRouting(btn.dataset.contractId, btn.dataset.filename));
            });

            // Download original document
            list.querySelectorAll(".btn-download-doc").forEach(btn => {
                btn.addEventListener("click", () => downloadOriginalDoc(btn.dataset.documentId, btn.dataset.filename));
            });

            // Navigate to review page on filename click
            list.querySelectorAll(".contract-name-link").forEach(link => {
                if (link.dataset.contractId) {
                    link.addEventListener("click", () => {
                        window.location.href = `review.html?contractId=${link.dataset.contractId}&documentId=${link.dataset.documentId}&name=${link.dataset.name}&status=${link.dataset.status}`;
                    });
                }
            });

            // Batch checkbox events
            list.querySelectorAll(".batch-checkbox").forEach(cb => {
                cb.addEventListener("change", () => {
                    const contractId = cb.dataset.contractId;
                    const row = cb.closest(".file-row");
                    if (cb.checked) {
                        batchSelectedIds.add(contractId);
                        row.classList.add("batch-selected");
                    } else {
                        batchSelectedIds.delete(contractId);
                        row.classList.remove("batch-selected");
                    }
                    updateBatchSignButton();
                });
            });

            // Fire analysis status fetch per row (non-blocking)
            files.forEach(f => {
                if (f.contract_id) {
                    loadAnalysisStatus(f.contract_id, f.contract_status || "uploaded");
                }
            });

            // For routed_for_signature contracts, fetch signers in parallel and
            // show the Sign button + batch checkbox only if it's the current user's turn.
            const currentUserEmail = getCurrentUserEmail();
            const routedContracts = files.filter(f => f.contract_id && (f.contract_status || f.status) === "routed_for_signature");
            if (currentUserEmail && routedContracts.length) {
                routedContracts.forEach(f => {
                    fetch(`${CONFIG.API_BASE_URL}/contracts/${f.contract_id}/signers`, {
                        headers: { Authorization: getIdToken() },
                    })
                    .then(res => res.ok ? res.json() : Promise.reject())
                    .then(data => {
                        const signers = data.signers || [];
                        // Find the lowest-order pending signer — that's who's turn it is
                        const pending = signers
                            .filter(s => s.status === "pending")
                            .sort((a, b) => a.order - b.order);
                        const isMyTurn = pending.length > 0 &&
                            pending[0].email.toLowerCase() === currentUserEmail;

                        const actionsCell = document.getElementById(`actions-${f.contract_id}`);
                        if (!actionsCell) return;

                        // Show/hide the Sign button
                        const signBtn = actionsCell.querySelector(".btn-sign");
                        if (signBtn) {
                            signBtn.style.display = isMyTurn ? "" : "none";
                        }

                        // Show/hide the batch checkbox column for this row
                        const row = actionsCell.closest(".file-row");
                        if (row) {
                            const checkLabel = row.querySelector(".batch-checkbox-label");
                            if (checkLabel) {
                                checkLabel.style.display = isMyTurn ? "" : "none";
                            }
                            // Remove batchable class so it won't be included in batch count
                            if (!isMyTurn) {
                                row.classList.remove("batchable");
                                const cb = row.querySelector(".batch-checkbox");
                                if (cb && cb.checked) {
                                    cb.checked = false;
                                    batchSelectedIds.delete(f.contract_id);
                                    row.classList.remove("batch-selected");
                                    updateBatchSignButton();
                                }
                            }
                        }
                    })
                    .catch(() => {
                        // On fetch failure, leave Sign button visible (fail open — user can still try)
                    });
                });
            }
        }

        // =============================================================
        // Analysis Status — fetch per row on load
        // =============================================================
        async function loadAnalysisStatus(contractId, contractStatus) {
            const statusLine = document.getElementById(`analysis-status-${contractId}`);
            const riskBadge = document.getElementById(`risk-badge-${contractId}`);

            try {
                const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis`, {
                    headers: { Authorization: getIdToken() },
                });

                if (!res.ok) {
                    if (statusLine) statusLine.innerHTML = "";
                    return;
                }

                const data = await res.json();
                const agents = data.agents || {};
                const risk = agents.riskClause || {};
                const fields = agents.templatePrepopulation || {};
                const summary = agents.summary || {};

                // Build status line parts
                const parts = [];

                if (risk.status === "succeeded" && risk.resultCount != null) {
                    parts.push(`<span class="analysis-stat analysis-stat-risk">⚠ ${risk.resultCount} risk${risk.resultCount !== 1 ? "s" : ""}</span>`);
                } else if (risk.status === "running" || risk.status === "pending") {
                    parts.push(`<span class="analysis-stat analysis-stat-pending">risks pending</span>`);
                }

                if (fields.status === "succeeded" && fields.resultCount != null) {
                    parts.push(`<span class="analysis-stat analysis-stat-fields">${fields.resultCount} field${fields.resultCount !== 1 ? "s" : ""}</span>`);
                }

                if (summary.status === "succeeded") {
                    parts.push(`<span class="analysis-stat analysis-stat-summary">summary ready</span>`);
                } else if (summary.status === "running" || summary.status === "pending") {
                    parts.push(`<span class="analysis-stat analysis-stat-pending">summary pending</span>`);
                }

                if (statusLine) {
                    statusLine.innerHTML = parts.length
                        ? parts.join('<span class="analysis-stat-sep">·</span>')
                        : "";
                }

                // Risk badge next to Send for Signature
                if (riskBadge && risk.status === "succeeded" && risk.resultCount != null && risk.resultCount > 0) {
                    riskBadge.innerHTML = `<span class="risk-count-badge">⚠ ${risk.resultCount} risk${risk.resultCount !== 1 ? "s" : ""}</span>`;
                }

            } catch (err) {
                if (statusLine) statusLine.innerHTML = "";
            }
        }

        function formatDate(iso) {
            if (!iso) return "";
            return new Date(iso).toLocaleDateString();
        }

        // =============================================================
        // Analysis Modal — 4 tabs: Risks / Fields / Summary / Obligations
        // =============================================================
        let analysisModalContractId = null;
        let analysisModalContractStatus = null;
        let analysisModalFilename = null;
        let activeAnalysisTab = "risks";

        const analysisModal = document.getElementById("analysis-modal");
        const analysisModalClose = document.getElementById("analysis-modal-close");

        analysisModalClose.addEventListener("click", () => analysisModal.classList.add("hidden"));
        analysisModal.addEventListener("click", e => { if (e.target === analysisModal) analysisModal.classList.add("hidden"); });

        // Download Analysis as PDF
        document.getElementById("btn-download-analysis-pdf").addEventListener("click", () => {
            downloadAnalysisAsPdf();
        });

        async function downloadAnalysisAsPdf() {
            const filename = analysisModalFilename || "contract";
            const contractId = analysisModalContractId;
            const contractStatus = analysisModalContractStatus;

            const btn = document.getElementById("btn-download-analysis-pdf");
            btn.disabled = true;
            btn.textContent = "Preparing...";

            try {
                // Fetch all 4 tabs in parallel
                const [riskRes, fieldsRes, summaryRes, obligationsRes] = await Promise.all([
                    fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis/risk-clause`, { headers: { Authorization: getIdToken() } }),
                    fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis/template-prepopulation`, { headers: { Authorization: getIdToken() } }),
                    fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis/summary`, { headers: { Authorization: getIdToken() } }),
                    contractStatus === "signed"
                        ? fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/analysis/obligation-tracking`, { headers: { Authorization: getIdToken() } })
                        : Promise.resolve(null),
                ]);

                const riskData       = riskRes.ok       ? await riskRes.json()        : null;
                const fieldsData     = fieldsRes.ok     ? await fieldsRes.json()      : null;
                const summaryData    = summaryRes.ok    ? await summaryRes.json()     : null;
                const obligationsData = obligationsRes && obligationsRes.ok ? await obligationsRes.json() : null;

                // Build printable HTML
                const printWindow = window.open("", "_blank");
                if (!printWindow) {
                    showToast("Pop-up blocked. Please allow pop-ups and try again.", "error");
                    return;
                }

                const risks = riskData?.risks || [];
                const fields = fieldsData?.fields || [];
                const obligations = obligationsData?.obligations || [];

                printWindow.document.write(`<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Analysis — ${escapeHtml(filename)}</title>
<style>
  body { font-family: Inter, Arial, sans-serif; font-size: 13px; color: #1a1a2e; margin: 32px; line-height: 1.5; }
  h1 { font-size: 20px; margin-bottom: 4px; }
  h2 { font-size: 15px; margin: 24px 0 10px; border-bottom: 2px solid #7c4dff; padding-bottom: 4px; color: #7c4dff; }
  .meta { font-size: 11px; color: #666; margin-bottom: 24px; }
  .risk-item, .obligation-item { border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px; margin-bottom: 10px; }
  .badge { display:inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; margin-right: 6px; }
  .high { background: #ffebee; color: #c62828; }
  .medium { background: #fff3e0; color: #e65100; }
  .low { background: #e8f5e9; color: #2e7d32; }
  .title { font-weight: 600; margin: 6px 0 4px; }
  .detail { color: #444; margin: 4px 0; }
  blockquote { border-left: 3px solid #7c4dff; padding: 4px 10px; margin: 6px 0; color: #555; font-style: italic; font-size: 12px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th { background: #f5f5f5; padding: 8px; text-align: left; font-size: 12px; border: 1px solid #ddd; }
  td { padding: 7px 8px; border: 1px solid #ddd; font-size: 12px; }
  .summary-text { background: #f9f9ff; border-radius: 6px; padding: 12px; margin-bottom: 12px; }
  ul { margin: 6px 0 0 16px; padding: 0; }
  li { margin-bottom: 4px; }
  @media print { body { margin: 16px; } }
</style>
</head>
<body>
<h1>Contract Analysis</h1>
<div class="meta">
  <strong>${escapeHtml(filename)}</strong> &nbsp;·&nbsp;
  Generated ${new Date().toLocaleDateString("en-US", { year:"numeric", month:"short", day:"numeric", hour:"2-digit", minute:"2-digit" })}
</div>

<h2>Risks (${risks.length})</h2>
${risks.length ? risks.map(r => `
<div class="risk-item">
  <div><span class="badge ${r.severity}">${r.severity}</span><span class="badge" style="background:#f3e5f5;color:#6a1b9a">${r.category || ""}</span></div>
  <div class="title">${escapeHtml(r.title)}</div>
  <div class="detail">${escapeHtml(r.detail)}</div>
  ${r.sourceQuote ? `<blockquote>"${escapeHtml(r.sourceQuote)}"${r.sourcePage ? ` — p.${r.sourcePage}` : ""}</blockquote>` : ""}
  ${r.suggestedLanguage ? `<div style="margin-top:6px;font-size:11px;color:#555"><strong>💡 Advisory suggestion:</strong> ${escapeHtml(r.suggestedLanguage)}</div>` : ""}
</div>`).join("") : "<p>No risks flagged.</p>"}

<h2>Summary</h2>
${summaryData?.summaryText ? `<div class="summary-text">${escapeHtml(summaryData.summaryText)}</div>` : "<p>No summary available.</p>"}
${summaryData?.keyPoints?.length ? `<strong>Key Points</strong><ul>${summaryData.keyPoints.map(kp => `<li>${escapeHtml(kp)}</li>`).join("")}</ul>` : ""}

<h2>Fields (${fields.length})</h2>
${fields.length ? `
<table>
  <tr><th>Type</th><th>Label</th><th>Role</th><th>Page</th><th>Required</th></tr>
  ${fields.map(f => `<tr><td>${escapeHtml(f.fieldType||"")}</td><td>${escapeHtml(f.label||"")}</td><td>${escapeHtml(f.signerRole||"")}</td><td>${f.page||"—"}</td><td>${f.isRequired?"Yes":"No"}</td></tr>`).join("")}
</table>` : "<p>No fields detected.</p>"}

<h2>Obligations (${obligations.length})</h2>
${obligations.length ? obligations.map(o => `
<div class="obligation-item">
  <div><span class="badge" style="background:#e8f5e9;color:#2e7d32">${escapeHtml(o.obligationType||"")}</span>${o.ownerParty ? `<span class="badge" style="background:#e3f2fd;color:#1565c0">Owner: ${escapeHtml(o.ownerParty)}</span>` : ""}${o.dueDate ? `<span class="badge" style="background:#fff3e0;color:#e65100">Due: ${escapeHtml(o.dueDate)}</span>` : ""}</div>
  <div class="detail">${escapeHtml(o.description||"")}</div>
  ${o.sourceQuote ? `<blockquote>"${escapeHtml(o.sourceQuote)}"</blockquote>` : ""}
</div>`).join("") : `<p>${contractStatus !== "signed" ? "Obligations are available after the contract is signed." : "No obligations found."}</p>`}

</body>
</html>`);
                printWindow.document.close();
                printWindow.focus();
                setTimeout(() => {
                    printWindow.print();
                }, 500);

            } catch (err) {
                showToast(`Export failed: ${err.message}`, "error");
            } finally {
                btn.disabled = false;
                btn.innerHTML = `<svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Download PDF`;
            }
        }

        document.querySelectorAll(".analysis-tab").forEach(tab => {
            tab.addEventListener("click", () => {
                document.querySelectorAll(".analysis-tab").forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                activeAnalysisTab = tab.dataset.tab;
                loadAnalysisTab(activeAnalysisTab);
            });
        });

        function openAnalysisModal(contractId, filename, contractStatus) {
            analysisModalContractId = contractId;
            analysisModalContractStatus = contractStatus;
            analysisModalFilename = filename;

            document.getElementById("analysis-modal-filename").textContent = filename;

            // Reset to Risks tab
            document.querySelectorAll(".analysis-tab").forEach(t => t.classList.remove("active"));
            document.querySelector('.analysis-tab[data-tab="risks"]').classList.add("active");
            activeAnalysisTab = "risks";

            analysisModal.classList.remove("hidden");
            loadAnalysisTab("risks");
        }

        async function loadAnalysisTab(tabName) {
            const body = document.getElementById("analysis-modal-body");
            body.innerHTML = `<div class="analysis-loading"><div class="loading-spinner"></div><span>Loading...</span></div>`;

            // Obligations tab: only available post-signing
            if (tabName === "obligations" && analysisModalContractStatus !== "signed") {
                body.innerHTML = `<div class="analysis-empty">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="36" height="36" style="opacity:0.3;margin-bottom:10px"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                    <p>Obligation tracking is only available after the contract is signed.</p>
                </div>`;
                return;
            }

            const agentTypeMap = {
                risks: "risk-clause",
                fields: "template-prepopulation",
                summary: "summary",
                obligations: "obligation-tracking",
            };

            try {
                const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${analysisModalContractId}/analysis/${agentTypeMap[tabName]}`, {
                    headers: { Authorization: getIdToken() },
                });

                if (res.status === 409) {
                    const err = await res.json();
                    const status = err.error?.status || "pending";
                    body.innerHTML = `<div class="analysis-empty">
                        <div class="analysis-pending-icon">⏳</div>
                        <p>Analysis is <strong>${status}</strong> for this contract.</p>
                        <p style="font-size:0.78rem;color:var(--text-muted);margin-top:4px">Check back after the page refreshes.</p>
                    </div>`;
                    return;
                }

                if (!res.ok) throw new Error(`Request failed (${res.status})`);

                const data = await res.json();

                if (tabName === "risks") renderRisksTab(data, body);
                else if (tabName === "fields") renderFieldsTab(data, body);
                else if (tabName === "summary") renderSummaryTab(data, body);
                else if (tabName === "obligations") renderObligationsTab(data, body);

            } catch (err) {
                body.innerHTML = `<div class="analysis-empty analysis-error">Failed to load: ${escapeHtml(err.message)}</div>`;
            }
        }

        function renderRisksTab(data, body) {
            const risks = data.risks || [];
            if (!risks.length) {
                body.innerHTML = `<div class="analysis-empty">No risks flagged for this contract.</div>`;
                return;
            }

            body.innerHTML = risks.map(r => `
                <div class="risk-item">
                    <div class="risk-item-header">
                        <span class="risk-severity risk-severity-${r.severity}">${r.severity}</span>
                        <span class="risk-category">${formatRiskCategory(r.category)}</span>
                        <span class="risk-title">${escapeHtml(r.title)}</span>
                    </div>
                    <p class="risk-detail">${escapeHtml(r.detail)}</p>
                    ${r.sourceQuote ? `<blockquote class="risk-quote">"${escapeHtml(r.sourceQuote)}"${r.sourcePage ? ` <span class="risk-page">p.${r.sourcePage}</span>` : ""}</blockquote>` : ""}
                    ${r.suggestedLanguage ? `
                        <div class="risk-suggestion">
                            <span class="risk-suggestion-label">💡 Advisory suggestion (not applied to document)</span>
                            <p class="risk-suggestion-text">${escapeHtml(r.suggestedLanguage)}</p>
                        </div>` : ""}
                </div>
            `).join("");
        }

        function renderFieldsTab(data, body) {
            const fields = data.fields || [];
            const templateType = data.detectedTemplateType || "unknown";
            const rationale = data.rationale || "";

            let html = `<div class="fields-header">
                <span class="fields-template-badge">${templateType.toUpperCase()}</span>
                ${rationale ? `<p class="fields-rationale">${escapeHtml(rationale)}</p>` : ""}
            </div>`;

            if (!fields.length) {
                html += `<div class="analysis-empty">No fields detected for this contract.</div>`;
                body.innerHTML = html;
                return;
            }

            html += `<div class="fields-table">
                <div class="fields-table-header">
                    <span>Type</span>
                    <span>Label</span>
                    <span>Role</span>
                    <span>Page</span>
                    <span>Required</span>
                </div>
                ${fields.map(f => `
                    <div class="fields-table-row">
                        <span class="field-type-badge">${escapeHtml(f.fieldType || "")}</span>
                        <span>${escapeHtml(f.label || "")}</span>
                        <span style="color:var(--text-secondary);font-size:0.8rem">${escapeHtml(f.signerRole || "")}</span>
                        <span style="color:var(--text-secondary);font-size:0.8rem">${f.page || "—"}</span>
                        <span>${f.isRequired ? '<span class="field-required">Yes</span>' : '<span style="color:var(--text-muted)">No</span>'}</span>
                    </div>
                `).join("")}
            </div>`;

            body.innerHTML = html;
        }

        function renderSummaryTab(data, body) {
            const summaryText = data.summaryText || "";
            const keyPoints = data.keyPoints || [];

            let html = "";

            if (summaryText) {
                html += `<div class="summary-text">${escapeHtml(summaryText)}</div>`;
            }

            if (keyPoints.length) {
                html += `<div class="summary-keypoints">
                    <p class="summary-keypoints-label">Key Points</p>
                    <ul class="summary-keypoints-list">
                        ${keyPoints.map(kp => `<li>${escapeHtml(kp)}</li>`).join("")}
                    </ul>
                </div>`;
            }

            if (!html) {
                html = `<div class="analysis-empty">No summary available for this contract.</div>`;
            }

            body.innerHTML = html;
        }

        function renderObligationsTab(data, body) {
            const obligations = data.obligations || [];
            if (!obligations.length) {
                body.innerHTML = `<div class="analysis-empty">No obligations found for this contract.</div>`;
                return;
            }

            body.innerHTML = obligations.map(o => `
                <div class="obligation-item">
                    <div class="obligation-item-header">
                        <span class="obligation-type-badge">${escapeHtml(o.obligationType || "")}</span>
                        ${o.ownerParty ? `<span class="obligation-owner">Owner: ${escapeHtml(o.ownerParty)}</span>` : ""}
                        ${o.dueDate
                            ? `<span class="obligation-due">Due: ${escapeHtml(o.dueDate)}</span>`
                            : o.rawDateText
                                ? `<span class="obligation-due">${escapeHtml(o.rawDateText)}</span>`
                                : ""}
                    </div>
                    <p class="obligation-desc">${escapeHtml(o.description || "")}</p>
                    ${o.sourceQuote ? `<blockquote class="risk-quote">"${escapeHtml(o.sourceQuote)}"${o.sourcePage ? ` <span class="risk-page">p.${o.sourcePage}</span>` : ""}</blockquote>` : ""}
                    ${o.recurrence && o.recurrence !== "oneTime" ? `<span class="obligation-recurrence">↺ ${escapeHtml(o.recurrence)}</span>` : ""}
                </div>
            `).join("");
        }

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

        // =============================================================
        // Route Modal — Assign Signers
        // =============================================================
        const routeModal = document.getElementById("route-modal");
        const routeModalFilename = document.getElementById("route-modal-filename");
        const routeModalCancel = document.getElementById("route-modal-cancel");
        const routeModalConfirm = document.getElementById("route-modal-confirm");

        function openRouteModal(contractId, filename) {
            selectedContractId = contractId;
            selectedContractName = filename;
            routeModalFilename.textContent = filename;
            renderSignerRows(2); // start with 2 rows by default
            routeModal.classList.remove("hidden");
        }

        const MAX_SIGNERS = 5;
        const SIGNER_ROLES = ["supplier", "company", "witness", "approver", "reviewer"];

        function renderSignerRows(count) {
            const form = document.getElementById("signers-form");
            form.innerHTML = "";
            for (let i = 1; i <= count; i++) {
                const row = document.createElement("div");
                row.className = "signer-row";
                row.dataset.index = i;
                row.innerHTML = `
                    <div style="display:flex;align-items:center;gap:8px;width:100%">
                        <div style="flex:1">
                            <label>Signer ${i}</label>
                            <input type="email" class="input-field signer-email-input" placeholder="email@example.com" data-index="${i}">
                        </div>
                        <div style="width:130px">
                            <label>Role</label>
                            <select class="input-field signer-role-select" data-index="${i}" style="padding:8px">
                                ${SIGNER_ROLES.map(r => `<option value="${r}"${i === 1 ? (r === "supplier" ? " selected" : "") : (i === 2 ? (r === "company" ? " selected" : "") : "")}>${r.charAt(0).toUpperCase() + r.slice(1)}</option>`).join("")}
                            </select>
                        </div>
                        ${count > 1 ? `<button class="btn-secondary btn-sm btn-remove-signer" data-index="${i}" style="margin-top:18px;padding:6px 10px;color:var(--color-error);border-color:rgba(239,83,80,0.3)" title="Remove signer">✕</button>` : ""}
                    </div>`;
                form.appendChild(row);
            }

            // Update Add Signer button visibility
            const addBtn = document.getElementById("btn-add-signer");
            if (addBtn) addBtn.style.display = count >= MAX_SIGNERS ? "none" : "";

            // Remove signer button events
            form.querySelectorAll(".btn-remove-signer").forEach(btn => {
                btn.addEventListener("click", () => {
                    const currentCount = form.querySelectorAll(".signer-row").length;
                    if (currentCount <= 1) return;
                    // Save current emails/roles before re-rendering
                    const emails = [...form.querySelectorAll(".signer-email-input")].map(i => i.value);
                    const roles  = [...form.querySelectorAll(".signer-role-select")].map(s => s.value);
                    const idx = parseInt(btn.dataset.index) - 1;
                    emails.splice(idx, 1);
                    roles.splice(idx, 1);
                    renderSignerRows(currentCount - 1);
                    // Restore values
                    form.querySelectorAll(".signer-email-input").forEach((inp, i) => { inp.value = emails[i] || ""; });
                    form.querySelectorAll(".signer-role-select").forEach((sel, i) => { sel.value = roles[i] || SIGNER_ROLES[i] || "supplier"; });
                });
            });
        }

        // Add Signer button
        document.getElementById("btn-add-signer").addEventListener("click", () => {
            const currentCount = document.getElementById("signers-form").querySelectorAll(".signer-row").length;
            if (currentCount >= MAX_SIGNERS) return;
            // Save current values before re-rendering
            const form = document.getElementById("signers-form");
            const emails = [...form.querySelectorAll(".signer-email-input")].map(i => i.value);
            const roles  = [...form.querySelectorAll(".signer-role-select")].map(s => s.value);
            renderSignerRows(currentCount + 1);
            form.querySelectorAll(".signer-email-input").forEach((inp, i) => { inp.value = emails[i] || ""; });
            form.querySelectorAll(".signer-role-select").forEach((sel, i) => { sel.value = roles[i] || SIGNER_ROLES[i] || "supplier"; });
        });

        routeModalCancel.addEventListener("click", () => routeModal.classList.add("hidden"));
        routeModal.addEventListener("click", e => { if (e.target === routeModal) routeModal.classList.add("hidden"); });

        routeModalConfirm.addEventListener("click", async () => {
            const form = document.getElementById("signers-form");
            const emailInputs = [...form.querySelectorAll(".signer-email-input")];
            const roleSelects = [...form.querySelectorAll(".signer-role-select")];

            const signers = emailInputs.map((inp, i) => ({
                email: inp.value.trim(),
                role: roleSelects[i]?.value || "supplier",
            }));

            // Validate — all emails must be filled
            if (signers.some(s => !s.email)) {
                showToast("Please fill in all signer emails", "error");
                return;
            }

            // Validate email format
            const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            if (signers.some(s => !emailRegex.test(s.email))) {
                showToast("One or more signer emails are invalid", "error");
                return;
            }

            routeModalConfirm.disabled = true;
            routeModalConfirm.textContent = "Sending...";

            try {
                const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${selectedContractId}/route`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Authorization: getIdToken(),
                    },
                    body: JSON.stringify({ signers }),
                });

                if (!res.ok) throw new Error("Failed to route contract");
                routeModal.classList.add("hidden");
                showToast("Contract sent for signature!", "success");
                loadContracts();
            } catch (err) {
                showToast(`Error: ${err.message}`, "error");
            } finally {
                routeModalConfirm.disabled = false;
                routeModalConfirm.textContent = "Send for Signature";
            }
        });

        // =============================================================
        // Signature Modal — Draw or Type
        // =============================================================
        const signatureModal = document.getElementById("signature-modal");
        const signatureModalFilename = document.getElementById("signature-modal-filename");
        const signatureModalCancel = document.getElementById("signature-modal-cancel");
        const signatureModalConfirm = document.getElementById("signature-modal-confirm");
        const drawCanvas = document.getElementById("signature-canvas");
        const typeCanvas = document.getElementById("signature-type-canvas");
        const drawCtx = drawCanvas.getContext("2d");
        const typeCtx = typeCanvas.getContext("2d");
        let isDrawing = false;
        let activeTab = "draw";
        let selectedFont = "Dancing Script";

        function openSignatureModal(contractId, filename) {
            selectedContractId = contractId;
            selectedContractName = filename;
            signatureModalFilename.textContent = filename;
            clearDrawCanvas();
            clearTypeCanvas();
            signatureModal.classList.remove("hidden");
        }

        signatureModalCancel.addEventListener("click", () => { batchSignMode = false; signatureModal.classList.add("hidden"); });
        signatureModal.addEventListener("click", e => { if (e.target === signatureModal) { batchSignMode = false; signatureModal.classList.add("hidden"); } });

        // Tabs
        document.querySelectorAll(".sig-tab").forEach(tab => {
            tab.addEventListener("click", () => {
                document.querySelectorAll(".sig-tab").forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                activeTab = tab.dataset.tab;
                document.getElementById("sig-tab-draw").classList.toggle("hidden", activeTab !== "draw");
                document.getElementById("sig-tab-type").classList.toggle("hidden", activeTab !== "type");
            });
        });

        // Draw canvas
        function clearDrawCanvas() {
            drawCtx.clearRect(0, 0, drawCanvas.width, drawCanvas.height);
            drawCtx.fillStyle = "#fff";
            drawCtx.fillRect(0, 0, drawCanvas.width, drawCanvas.height);
        }

        document.getElementById("btn-clear-signature").addEventListener("click", clearDrawCanvas);

        drawCanvas.addEventListener("mousedown", e => { isDrawing = true; drawCtx.beginPath(); drawCtx.moveTo(...getPos(e, drawCanvas)); });
        drawCanvas.addEventListener("mousemove", e => { if (!isDrawing) return; drawCtx.lineTo(...getPos(e, drawCanvas)); drawCtx.strokeStyle = "#1a1a2e"; drawCtx.lineWidth = 2; drawCtx.lineCap = "round"; drawCtx.stroke(); });
        drawCanvas.addEventListener("mouseup", () => isDrawing = false);
        drawCanvas.addEventListener("mouseleave", () => isDrawing = false);

        // Touch support
        drawCanvas.addEventListener("touchstart", e => { e.preventDefault(); isDrawing = true; drawCtx.beginPath(); drawCtx.moveTo(...getPos(e.touches[0], drawCanvas)); });
        drawCanvas.addEventListener("touchmove", e => { e.preventDefault(); if (!isDrawing) return; drawCtx.lineTo(...getPos(e.touches[0], drawCanvas)); drawCtx.strokeStyle = "#1a1a2e"; drawCtx.lineWidth = 2; drawCtx.lineCap = "round"; drawCtx.stroke(); });
        drawCanvas.addEventListener("touchend", () => isDrawing = false);

        function getPos(e, canvas) {
            const rect = canvas.getBoundingClientRect();
            const scaleX = canvas.width / rect.width;
            const scaleY = canvas.height / rect.height;
            return [(e.clientX - rect.left) * scaleX, (e.clientY - rect.top) * scaleY];
        }

        // Type signature
        const sigTextInput = document.getElementById("signature-text");

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

        document.querySelectorAll(".sig-font-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".sig-font-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                selectedFont = btn.dataset.font;
                renderTypeSignature();
            });
        });

        // Sign confirm
        signatureModalConfirm.addEventListener("click", async () => {
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

            signatureModalConfirm.disabled = true;
            signatureModalConfirm.textContent = "Signing...";

            // Close signature modal first
            signatureModal.classList.add("hidden");

            if (batchSignMode) {
                batchSignMode = false;
                await executeBatchSign(signatureData);
            } else {
                try {
                    const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${selectedContractId}/sign`, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            Authorization: getIdToken(),
                        },
                        body: JSON.stringify({ signatureData }),
                    });

                    if (!res.ok) throw new Error("Failed to sign contract");
                    const data = await res.json();

                    if (data.status === "signed") {
                        showToast("Contract fully signed!", "success");
                    } else {
                        showToast(`Signature recorded. ${data.pendingSigners} signer(s) remaining.`, "success");
                    }
                    loadContracts();
                } catch (err) {
                    showToast(`Error: ${err.message}`, "error");
                }
            }

            signatureModalConfirm.disabled = false;
            signatureModalConfirm.textContent = "Sign";
        });

        // =============================================================
        // Signers Status Modal
        // =============================================================
        const signersModal = document.getElementById("signers-modal");
        const signersModalFilename = document.getElementById("signers-modal-filename");
        const signersModalClose = document.getElementById("signers-modal-close");

        signersModalClose.addEventListener("click", () => signersModal.classList.add("hidden"));
        signersModal.addEventListener("click", e => { if (e.target === signersModal) signersModal.classList.add("hidden"); });

        async function openSignersModal(contractId, filename) {
            signersModalFilename.textContent = filename;
            document.getElementById("signers-list").innerHTML = `<div class="files-loading"><div class="loading-spinner"></div><span>Loading...</span></div>`;
            signersModal.classList.remove("hidden");

            try {
                const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/signers`, {
                    headers: { Authorization: getIdToken() },
                });
                if (!res.ok) throw new Error("Failed to load signers");
                const data = await res.json();
                renderSignersList(data.signers || []);
            } catch (err) {
                document.getElementById("signers-list").innerHTML = `<div class="files-empty">Failed to load signers: ${err.message}</div>`;
            }
        }

        function renderSignersList(signers) {
            const list = document.getElementById("signers-list");
            if (!signers.length) {
                list.innerHTML = `<div class="files-empty">No signers assigned yet.</div>`;
                return;
            }
            list.innerHTML = signers.map(s => `
                <div class="signer-item">
                    <div class="signer-item-info">
                        <div class="signer-item-email">${escapeHtml(s.email)}</div>
                        <div class="signer-item-role">${s.role} · Order ${s.order}</div>
                    </div>
                    <span class="signer-item-status ${s.status}">${s.status}</span>
                </div>
            `).join("");
        }

        // =============================================================
        // Cancel Routing
        // =============================================================
        async function confirmCancelRouting(contractId, filename) {
            if (!confirm(`Cancel routing for "${filename}"?\n\nThis will remove all assigned signers and reset the contract to "Ready for Review" so you can re-send it to the correct signers.`)) {
                return;
            }

            try {
                const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/cancel-routing`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Authorization: getIdToken(),
                    },
                });

                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.error || "Failed to cancel routing");
                }

                showToast("Routing cancelled. Contract is ready to re-send.", "success");
                loadContracts();
            } catch (err) {
                showToast(`Error: ${err.message}`, "error");
            }
        }

        // =============================================================
        // Download Original Document
        // =============================================================
        async function downloadOriginalDoc(documentId, filename) {
            try {
                const res = await fetch(`${CONFIG.API_BASE_URL}/files/${documentId}/download`, {
                    headers: { Authorization: getIdToken() },
                });

                if (!res.ok) throw new Error("Failed to get download link");

                const data = await res.json();
                const a = document.createElement("a");
                a.href = data.download_url;
                a.download = filename;
                a.target = "_blank";
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
            } catch (err) {
                showToast(`Download failed: ${err.message}`, "error");
            }
        }

        // =============================================================
        // Batch Signing
        // =============================================================
        const batchResultModal = document.getElementById("batch-result-modal");
        const batchResultClose = document.getElementById("batch-result-close");

        batchResultClose.addEventListener("click", () => {
            batchResultModal.classList.add("hidden");
            loadContracts();
        });
        batchResultModal.addEventListener("click", e => {
            if (e.target === batchResultModal) {
                batchResultModal.classList.add("hidden");
                loadContracts();
            }
        });

        function openBatchSignatureModal() {
            if (batchSelectedIds.size === 0) return;
            batchSignMode = true;

            // Reuse existing signature modal
            const filenameEl = document.getElementById("signature-modal-filename");
            filenameEl.textContent = `${batchSelectedIds.size} contract${batchSelectedIds.size !== 1 ? "s" : ""} selected`;

            clearDrawCanvas();
            clearTypeCanvas();
            signatureModal.classList.remove("hidden");
        }

        async function executeBatchSign(signatureData) {
            const ids = Array.from(batchSelectedIds);
            const results = { signed: [], failed: [] };

            // Show progress in result modal
            batchResultModal.classList.remove("hidden");
            const body = document.getElementById("batch-result-body");
            body.innerHTML = `<div class="batch-progress">
                <div class="loading-spinner"></div>
                <span>Signing ${ids.length} contract${ids.length !== 1 ? "s" : ""}...</span>
            </div>`;

            for (const contractId of ids) {
                // Find the filename for this contractId
                const row = document.querySelector(`.file-row[data-contract-id="${contractId}"]`);
                const filename = row ? row.dataset.name : contractId;

                try {
                    const res = await fetch(`${CONFIG.API_BASE_URL}/contracts/${contractId}/sign`, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            Authorization: getIdToken(),
                        },
                        body: JSON.stringify({ signatureData }),
                    });

                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.error || `Status ${res.status}`);
                    }

                    results.signed.push({ contractId, filename });
                } catch (err) {
                    results.failed.push({ contractId, filename, error: err.message });
                }
            }

            renderBatchResults(results, body);
        }

        function renderBatchResults(results, body) {
            const total = results.signed.length + results.failed.length;
            const allGood = results.failed.length === 0;

            let html = `<div class="batch-summary ${allGood ? "batch-summary-success" : "batch-summary-partial"}">
                <span class="batch-summary-icon">${allGood ? "✅" : "⚠️"}</span>
                <span class="batch-summary-text">
                    <strong>${results.signed.length} of ${total}</strong> contract${total !== 1 ? "s" : ""} signed successfully
                    ${results.failed.length > 0 ? `· <strong>${results.failed.length}</strong> failed` : ""}
                </span>
            </div>`;

            if (results.signed.length > 0) {
                html += `<div class="batch-result-section">
                    <p class="batch-result-section-label">✅ Signed</p>
                    ${results.signed.map(r => `
                        <div class="batch-result-row batch-result-signed">
                            <svg class="batch-result-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="20 6 9 17 4 12"/></svg>
                            <span>${escapeHtml(r.filename)}</span>
                        </div>`).join("")}
                </div>`;
            }

            if (results.failed.length > 0) {
                html += `<div class="batch-result-section">
                    <p class="batch-result-section-label">❌ Failed — re-submit individually</p>
                    ${results.failed.map(r => `
                        <div class="batch-result-row batch-result-failed">
                            <svg class="batch-result-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                            <span>${escapeHtml(r.filename)}</span>
                            <span class="batch-result-error">${escapeHtml(r.error)}</span>
                        </div>`).join("")}
                </div>`;
            }

            body.innerHTML = html;
        }

    }); // end DOMContentLoaded
})();
