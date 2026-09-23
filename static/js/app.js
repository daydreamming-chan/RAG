(function () {
    const API = "/api";

    let state = {
        sessions: [],
        activeSessionId: null,
        streamingAbort: null,
    };

    const $ = (sel) => document.querySelector(sel);

    const sessionList = $("#session-list");
    const messagesEl = $("#messages");
    const userInput = $("#user-input");
    const btnSend = $("#btn-send");
    const btnNew = $("#btn-new-session");
    const sessionTitle = $("#current-session-title");
    const docList = $("#doc-list");
    const fileInput = $("#file-input");
    const uploadStatus = $("#upload-status");

    //api请求方法
    async function api(method, path, body) {
        const opts = {
            method,
            headers: { "Content-Type": "application/json" },
        };
        if (body) opts.body = JSON.stringify(body);
        const res = await fetch(API + path, opts);
        return res.json();
    }

    //会话管理
    async function loadSessions() {
        state.sessions = await api("GET", "/sessions");
        renderSessions();
    }

    function renderSessions() {
        sessionList.innerHTML = "";
        state.sessions.forEach((s) => {
            const li = document.createElement("li");
            li.dataset.id = s.id;
            if (s.id === state.activeSessionId) 
                li.classList.add("active");

            const span = document.createElement("span");
            span.className = "session-title";
            span.textContent = s.title || "未命名";
            span.title = "单击打开，双击重命名";
            span.onclick = () => switchSession(s.id);
            span.ondblclick = (e) => {
                e.stopPropagation();
                inlineRename(span, s.id, s.title || "", "session-title-input", false);
            };

            const actions = document.createElement("span");
            actions.className = "session-actions";

            const ren = document.createElement("span");
            ren.className = "rename-session";
            ren.textContent = "✎";
            ren.title = "重命名";
            ren.onclick = (e) => {
                e.stopPropagation();
                inlineRename(span, s.id, s.title || "", "session-title-input", false);
            };

            const del = document.createElement("span");
            del.className = "del-session";
            del.textContent = "×";
            del.title = "删除会话";
            del.onclick = (e) => {
                e.stopPropagation();
                deleteSession(s.id);
            };

            actions.appendChild(ren);
            actions.appendChild(del);

            li.appendChild(span);
            li.appendChild(actions);
            sessionList.appendChild(li);
        });
    }

    //只切换高亮，不重建 DOM：否则双击事件会因为元素被替换而丢失
    function highlightActive() {
        sessionList.querySelectorAll("li").forEach((li) => {
            li.classList.toggle("active", li.dataset.id === state.activeSessionId);
        });
    }

    async function switchSession(id) {
        state.activeSessionId = id;
        const sess = await api("GET", "/sessions/" + id);
        messagesEl.innerHTML = "";
        if (sess && sess.messages) {
            sess.messages.forEach((m) => addMessage(m.role, m.content));
        }
        setHeaderTitle(sess ? sess.title : "Mini-RAG");
        scrollBottom();
        btnSend.disabled = false;
        highlightActive();
    }

    async function deleteSession(id) {
        await api("DELETE", "/sessions/" + id);
        // 本地移除即可，不必回源重新拉整个列表
        state.sessions = state.sessions.filter((s) => s.id !== id);
        if (state.activeSessionId === id) {
            state.activeSessionId = null;
            messagesEl.innerHTML = "";
            setHeaderTitle("选择一个会话", false);
            // 不锁发送按钮：不选会话时输入内容会自动创建新会话
        }
        renderSessions();
    }

    /* ─── 标题：本地即时更新 + 重命名 ─── */

    //标题栏显示；renamable 控制是否可点击改名
    function setHeaderTitle(title, renamable = true) {
        sessionTitle.textContent = title;
        sessionTitle.classList.toggle("renamable", renamable);
    }

    //只改本地 state 后重渲染：标题变化不再需要重新拉列表或刷新页面
    function applyTitle(id, title) {
        const s = state.sessions.find((x) => x.id === id);
        if (s) s.title = title;
        if (state.activeSessionId === id) setHeaderTitle(title);
        renderSessions();
    }

    //新建的会话直接插到列表首位，省掉一次 GET /sessions
    function addSessionLocally(sess) {
        state.sessions.unshift({
            id: sess.id,
            title: sess.title,
            created_at: sess.created_at,
            message_count: 0,
        });
        renderSessions();
    }

    //保存标题；失败就回滚成旧标题
    async function saveTitle(id, title, oldTitle) {
        const res = await api("PATCH", "/sessions/" + id, { title });
        if (!res || res.error) {
            applyTitle(id, oldTitle);
            return;
        }
        applyTitle(id, res.title || title);
    }

    /*
     * 就地重命名：把 container 的内容换成输入框
     * Enter / 失焦 = 保存，Esc = 取消
     * inHeader=true 表示改的是顶部标题栏，否则替换侧边栏那一项
     */
    function inlineRename(container, id, oldTitle, inputClass, inHeader) {
        if (container.querySelector("input")) return;

        const input = document.createElement("input");
        input.className = inputClass;
        input.value = oldTitle;
        input.maxLength = 60;

        const restore = () => {
            if (inHeader) setHeaderTitle(oldTitle);
            else renderSessions();
        };

        if (inHeader) {
            container.textContent = "";
            container.appendChild(input);
        } else {
            container.replaceWith(input);
        }
        input.focus();
        input.select();

        //防止 blur 和键盘事件重复触发保存
        let finished = false;

        const finish = (value) => {
            if (finished) return;
            finished = true;
            const title = value.trim();
            if (!title || title === oldTitle) {
                restore();
                return;
            }
            saveTitle(id, title, oldTitle);
        };

        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                finish(input.value);
            } else if (e.key === "Escape") {
                e.preventDefault();
                finished = true;
                restore();
            }
        });
        input.addEventListener("blur", () => finish(input.value));
        //点击输入框不要冒泡出去，否则会触发切换会话 / 再次进入重命名
        input.addEventListener("click", (e) => e.stopPropagation());
        input.addEventListener("dblclick", (e) => e.stopPropagation());
    }

    //消息渲染辅助函数
    function addMessage(role, content, idHint) {
        const div = document.createElement("div");
        div.className = `message ${role}`;
        if (idHint) div.id = idHint;
        div.textContent = content;
        messagesEl.appendChild(div);
        scrollBottom();
        return div;
    }

    //保证消息区滚轮到最底部，每次进入会话为最新
    function scrollBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    /* ─── Documents ─── */
    async function loadDocuments() {
        const docs = await api("GET", "/documents");
        docList.innerHTML = "";
        docs.forEach((d) => {
            const li = document.createElement("li");
            li.innerHTML = `<span>${d.filename} (${d.chunk_count}块)</span>`;
            const del = document.createElement("span");
            del.className = "del-doc";
            del.textContent = "×";
            del.onclick = async () => {
                await api("DELETE", "/documents/" + encodeURIComponent(d.id));
                loadDocuments();
            };
            li.appendChild(del);
            docList.appendChild(li);
        });
    }

    fileInput.addEventListener("change", async () => {
        const file = fileInput.files[0];
        if (!file) return;
        uploadStatus.textContent = "上传中...";
        const form = new FormData();
        form.append("file", file);
        const res = await fetch(API + "/documents/upload", { method: "POST", body: form });
        const data = await res.json();
        uploadStatus.textContent = data.error ? "上传失败" : "上传完成";
        fileInput.value = "";
        setTimeout(() => { uploadStatus.textContent = ""; }, 2000);
        loadDocuments();
    });


    async function sendMessage() {
        const query = userInput.value.trim();
        if (!query) return;

        // 没有活跃会话时自动创建
        if (!state.activeSessionId) {
            const sess = await api("POST", "/sessions");
            state.activeSessionId = sess.id;
            setHeaderTitle(sess.title);
            addSessionLocally(sess);
        }

        addMessage("user", query);
        userInput.value = "";
        userInput.style.height = "auto";

        // 创建一个占位机器人气泡
        const botDiv = addMessage("assistant", "思考中...", "streaming-msg");
        btnSend.disabled = true;

        const ctrl = new AbortController();
        state.streamingAbort = ctrl;

        try {
            const res = await fetch(API + "/chat/stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    message: query,
                    session_id: state.activeSessionId,
                }),
                signal: ctrl.signal,
            });

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let started = false;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop();  // 保留未完成的行

                for (const line of lines) {
                    if (!line.startsWith("data: ")) continue;
                    const raw = line.slice(6).trim();
                    if (!raw || raw === "[DONE]") continue;

                    try {
                        const data = JSON.parse(raw);
                        if (data.type === "content") {
                            if (!started) {
                                botDiv.textContent = "";
                                started = true;
                            }
                            botDiv.textContent += data.text;
                            scrollBottom();
                        } else if (data.type === "error") {
                            if (!started) {
                                botDiv.textContent = "";
                                started = true;
                            }
                            botDiv.textContent += `[错误: ${data.message}]`;
                        } else if (data.type === "meta") {
                            // 检索元信息，不渲染文本
                        } else if (data.type === "title") {
                            // 后端首轮对话后自动改了标题 → 就地更新，不用刷新页面
                            applyTitle(data.session_id || state.activeSessionId, data.title);
                        }
                    } catch (e) { }
                }
            }

            if (!started) botDiv.textContent = "(已终止回答)";
        } catch (err) {
            if (err.name === "AbortError") {
                botDiv.textContent += "\n(已终止回答)";
            } else {
                botDiv.textContent = `请求失败: ${err.message}`;
            }
        } finally {
            state.streamingAbort = null;
            btnSend.disabled = false;
            botDiv.removeAttribute("id");
            // 标题由后端随流下推的 title 事件就地更新，这里不再回源刷新
        }
    }



    //新会话按钮事件绑定与初始化
    btnNew.addEventListener("click", async () => {
        const sess = await api("POST", "/sessions");
        state.activeSessionId = sess.id;
        messagesEl.innerHTML = "";
        setHeaderTitle(sess.title);
        btnSend.disabled = false;
        addSessionLocally(sess);
    });

    //点击顶部标题栏也能重命名当前会话
    sessionTitle.addEventListener("click", () => {
        if (!state.activeSessionId) return;
        inlineRename(sessionTitle, state.activeSessionId, sessionTitle.textContent, "header-title-input", true);
    });

    btnSend.addEventListener("click", sendMessage);

    userInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (!btnSend.disabled) sendMessage();
        }
    });

    userInput.addEventListener("input", () => {
        userInput.style.height = "auto";
        userInput.style.height = Math.min(userInput.scrollHeight, 200) + "px";
    });


    async function init() {
        await loadSessions();
        await loadDocuments();
    }

    init();

})()