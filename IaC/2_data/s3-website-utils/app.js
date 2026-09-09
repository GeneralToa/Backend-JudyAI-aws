// =============================================================
// Judy.ai Knowledge Base - Application Logic
// =============================================================

document.addEventListener("DOMContentLoaded", () => {
    if (!isAuthenticated()) return;

    // --- Navigation ---
    const navUpload = document.getElementById("nav-upload");
    const navChat = document.getElementById("nav-chat");
    const navFiles = document.getElementById("nav-files");
    const sectionUpload = document.getElementById("section-upload");
    const sectionChat = document.getElementById("section-chat");
    const sectionFiles = document.getElementById("section-files");
    const btnLogout = document.getElementById("btn-logout");

    function setActiveSection(sectionName) {
        navUpload.classList.remove("active");
        navChat.classList.remove("active");
        navFiles.classList.remove("active");
        sectionUpload.classList.remove("active");
        sectionChat.classList.remove("active");
        sectionFiles.classList.remove("active");

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
        }
    }

    navUpload.addEventListener("click", () => setActiveSection("upload"));
    navChat.addEventListener("click", () => setActiveSection("chat"));
    navFiles.addEventListener("click", () => setActiveSection("files"));

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
        const MAX_FILE_SIZE = 15 * 1024 * 1024;

        if (file.size > MAX_FILE_SIZE) {
            addStatus(`${file.name}: exceeds 15 MB limit (${(file.size / 1024 / 1024).toFixed(1)} MB)`, "error");
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
});
