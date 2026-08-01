// /Pokemon/public/js/admin_function.js

function useAdminUpdate() {
    const { ref, reactive, computed } = Vue;
    
    // === 卡牌更新相關 ===
    const showUpdateModal = ref(false);
    const uiState = ref('idle'); // idle, checking, ready, updating, formatting
    const versionInfo = reactive({
        localCount: 0,
        lastUpdate: '',
        officialCount: 0,
        officialPages: 0,
        diff: 0
    });
    const customUrl = ref("");
    
    // [新增] 擴充包與賽制選擇
    const expansionList = ref([]);
    const expansionGrouped = ref([]); // [新增] 系列分組
    const selectedExpansions = ref([]);
    const selectedRegulations = ref([1, 2]); 
    const updateJapanese = ref(false); // [新增] 日文更新開關
    const skipImages = ref(false); // [新增] 跳過圖片更新
    const loadingExpansions = ref(false);

    const updateState = reactive({
        running: false,
        progress: 0,
        message: '就緒',
        logs: []
    });
    let updatePollTimer = null;

    // === 用戶管理相關 ===
    const showAdminPanel = ref(false);
    const adminUsers = ref([]);
    const adminUsersLoading = ref(false);
    const adminUserSearch = ref('');
    const adminStats = reactive({
        totalUsers: 0,
        adminCount: 0,
        verifiedCount: 0,
        unverifiedCount: 0
    });

    // 過濾後的用戶列表
    const filteredAdminUsers = computed(() => {
        if (!adminUserSearch.value.trim()) return adminUsers.value;
        const query = adminUserSearch.value.toLowerCase();
        return adminUsers.value.filter(u => 
            u.username.toLowerCase().includes(query) || 
            (u.email && u.email.toLowerCase().includes(query))
        );
    });

    // 格式化日期
    const formatDate = (dateStr) => {
        if (!dateStr) return '-';
        try {
            const date = new Date(dateStr);
            return date.toLocaleDateString('zh-TW', { 
                year: 'numeric', 
                month: '2-digit', 
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            });
        } catch {
            return dateStr;
        }
    };

    // === 卡牌更新功能 ===
    const pollUpdateStatus = async () => {
        if (updatePollTimer) clearTimeout(updatePollTimer);
        try {
            const res = await fetch('/api/crawler/status');
            if (!res.ok) {
                console.warn("Server not reachable or permission denied");
                return;
            }
            const data = await res.json();
            
            updateState.running = data.running;
            updateState.progress = data.progress;
            updateState.message = data.message;
            updateState.logs = data.logs;

            if (data.running) {
                uiState.value = 'updating';
                updatePollTimer = setTimeout(pollUpdateStatus, 1000); 
            } else {
                if (uiState.value === 'updating') {
                    checkVersion();
                }
            }
        } catch (e) {
            console.error("Poll error", e);
        }
    };

    const fetchExpansions = async () => {
        loadingExpansions.value = true;
        try {
            const res = await fetch('/api/crawler/expansions');
            const data = await res.json();
            if (data.success) {
                // 支援新分組格式
                expansionList.value = data.expansions || [];
                expansionGrouped.value = data.grouped || [];
                // 預設選取最新的擴充包
                if (expansionList.value.length > 0 && selectedExpansions.value.length === 0) {
                    selectedExpansions.value = [expansionList.value[0].code];
                }
            }
        } catch (e) {
            console.error(e);
        } finally {
            loadingExpansions.value = false;
        }
    };

    // =========================================
    // JP 卡牌更新 (Limitless TCG)
    // =========================================
    const showJPUpdateModal = ref(false);
    const jpUpdateState = reactive({
        running: false,
        progress: 0,
        message: '就緒',
        logs: [],
        current_set: '',
        completed_sets: 0,
        total_sets: 0,
    });
    const jpTestSetCode = ref('SV8');
    const jpTestNumber = ref('1');
    const jpTestResult = ref(null);
    const jpExpansionList = ref([]);       // [{code, name, card_count}, ...]
    const jpSelectedExpansions = ref([]);
    const jpSelectAll = ref(false);
    const jpLoadingExpansions = ref(false);
    const jpWorkers = ref(5);
    const jpDelay = ref(0.3);
    let jpPollTimer = null;

    const openJPUpdateModal = () => {
        showJPUpdateModal.value = true;
        jpTestResult.value = null;
        fetchJPExpansions();
    };

    const fetchJPExpansions = async () => {
        jpLoadingExpansions.value = true;
        try {
            const res = await fetch('/api/limitless-jp/sets');
            const data = await res.json();
            if (data.status === 'success') {
                jpExpansionList.value = data.sets || [];
            } else {
                jpExpansionList.value = [];
            }
        } catch (e) {
            console.error(e);
        } finally {
            jpLoadingExpansions.value = false;
        }
    };

    const toggleSelectAllJP = () => {
        if (jpSelectedExpansions.value.length === jpExpansionList.value.length) {
            jpSelectedExpansions.value = [];
        } else {
            jpSelectedExpansions.value = jpExpansionList.value.map(e => e.code);
        }
    };

    const startJPSingleSet = (setCode, cardCount) => {
        if (jpUpdateState.running) return;
        _startJPCrawl({ target: setCode, card_count: cardCount, workers: jpWorkers.value, delay: jpDelay.value });
    };

    const startJPSelected = () => {
        if (jpUpdateState.running) return;
        if (jpSelectedExpansions.value.length === 0) {
            alert('請至少選擇一個系列');
            return;
        }
        // 將所有已選系列一次傳給後端依序爬取
        const codes = [...jpSelectedExpansions.value];
        const cardCounts = {};
        for (const exp of jpExpansionList.value) {
            if (codes.includes(exp.code)) {
                cardCounts[exp.code] = exp.card_count;
            }
        }
        _startJPCrawl({
            targets: codes,
            card_counts: cardCounts,
            workers: jpWorkers.value,
            delay: jpDelay.value,
        });
    };

    const startJPAll = () => {
        if (jpUpdateState.running) return;
        if (!confirm(`確定要爬取全部 ${jpExpansionList.value.length} 個系列嗎？這可能需要數小時。`)) return;
        _startJPCrawl({ target: 'all', workers: jpWorkers.value, delay: jpDelay.value });
    };

    const _startJPCrawl = async (body) => {
        try {
            const res = await fetch('/api/limitless-jp/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (data.status === 'success') {
                pollJPUpdateStatus();
            } else {
                alert(data.msg || '啟動失敗');
            }
        } catch (e) {
            console.error(e);
        }
    };

    const pollJPUpdateStatus = async () => {
        if (jpPollTimer) clearTimeout(jpPollTimer);
        try {
            const res = await fetch('/api/limitless-jp/status');
            const data = await res.json();
            jpUpdateState.running = data.is_running;
            jpUpdateState.progress = data.progress || 0;
            jpUpdateState.message = data.message || '';
            jpUpdateState.logs = (data.logs || []).map(l => typeof l === 'string' ? l : `[${l.time}] ${l.msg}`);
            jpUpdateState.current_set = data.current_set || '';
            jpUpdateState.completed_sets = data.completed_sets || 0;
            jpUpdateState.total_sets = data.total_sets || 0;

            if (data.is_running) {
                jpPollTimer = setTimeout(pollJPUpdateStatus, 1500);
            }
        } catch (e) {
            console.error("JP poll error", e);
        }
    };

    const jpTestCard = async () => {
        jpTestResult.value = { loading: true };
        try {
            const res = await fetch('/api/limitless-jp/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ set_code: jpTestSetCode.value, number: jpTestNumber.value })
            });
            const data = await res.json();
            jpTestResult.value = data;
        } catch (e) {
            jpTestResult.value = { status: 'error', msg: '連線失敗' };
        }
    };

    const openUpdateModal = () => {
        showUpdateModal.value = true;
        uiState.value = 'idle'; 
        pollUpdateStatus();
        if(expansionList.value.length === 0) fetchExpansions();
    };

    const checkVersion = async () => {
        uiState.value = 'checking';
        try {
            const res = await fetch('/api/admin/check_version');
            const data = await res.json();
            if (data.success) {
                versionInfo.localCount = data.local.total_cards;
                versionInfo.lastUpdate = data.local.last_update;
                versionInfo.officialCount = data.official.total_cards;
                versionInfo.officialPages = data.official.total_pages;
                versionInfo.diff = data.official.total_cards - data.local.total_cards;
                uiState.value = 'ready';
            } else {
                alert("檢查失敗: " + (data.error || '權限不足'));
                uiState.value = 'idle';
            }
        } catch (e) {
            alert("連線錯誤");
            uiState.value = 'idle';
        }
    };

    const startUpdate = async () => {
        if (updateState.running) return;
        if(selectedExpansions.value.length === 0) {
            alert('請至少選擇一個擴充包');
            return;
        }

        try {
            const res = await fetch('/api/crawler/start', { 
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    target_expansion_codes: selectedExpansions.value,
                    target_regulations: selectedRegulations.value,
                    update_japanese: updateJapanese.value,
                    skip_images: skipImages.value
                })
            });
            const data = await res.json();
            if (data.success) {
                uiState.value = 'updating';
                updateState.running = true;
                updateState.progress = 0;
                pollUpdateStatus();
            } else {
                alert(data.message || data.error);
            }
        } catch (e) {
            alert("啟動失敗: " + e.message);
        }
    };

    // === 用戶管理功能 ===
    const openAdminPanel = async () => {
        showAdminPanel.value = true;
        await loadAdminUsers();
    };

    const loadAdminUsers = async () => {
        adminUsersLoading.value = true;
        try {
            const res = await fetch('/api/admin/users');
            const data = await res.json();
            if (data.success) {
                adminUsers.value = data.users;
                // 計算統計
                adminStats.totalUsers = data.users.length;
                adminStats.adminCount = data.users.filter(u => u.role === 'admin').length;
                adminStats.verifiedCount = data.users.filter(u => u.is_verified).length;
                adminStats.unverifiedCount = data.users.filter(u => !u.is_verified).length;
            } else {
                alert("載入失敗: " + (data.error || '權限不足'));
            }
        } catch (e) {
            alert("連線錯誤");
        } finally {
            adminUsersLoading.value = false;
        }
    };

    const filterAdminUsers = () => {
        // 已由 computed 屬性處理
    };

    const toggleUserRole = async (targetUser) => {
        const newRole = targetUser.role === 'admin' ? 'user' : 'admin';
        const action = newRole === 'admin' ? '升級為管理員' : '降級為普通用戶';
        
        if (!confirm(`確定要將 "${targetUser.username}" ${action}？`)) return;
        
        try {
            const res = await fetch('/api/admin/users/role', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: targetUser.id, role: newRole })
            });
            const data = await res.json();
            if (data.success) {
                targetUser.role = newRole;
                // 更新統計
                if (newRole === 'admin') {
                    adminStats.adminCount++;
                } else {
                    adminStats.adminCount--;
                }
                alert(`已成功將 "${targetUser.username}" ${action}`);
            } else {
                alert("操作失敗: " + data.error);
            }
        } catch (e) {
            alert("連線錯誤");
        }
    };

    const toggleAiAccess = async (targetUser) => {
        const next = !targetUser.ai_enabled;
        const action = next ? '開放 AI 助手使用權' : '停用 AI 助手使用權';
        if (!confirm(`確定要對 "${targetUser.username}" ${action}？`)) return;

        try {
            const res = await fetch(`/api/admin/users/${targetUser.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ai_enabled: next })
            });
            const data = await res.json();
            if (data.success) {
                targetUser.ai_enabled = next;
                alert(`已成功對 "${targetUser.username}" ${action}`);
            } else {
                alert("操作失敗: " + data.error);
            }
        } catch (e) {
            alert("連線錯誤");
        }
    };

    const verifyUser = async (targetUser) => {
        if (!confirm(`確定要手動驗證 "${targetUser.username}" 的帳號？`)) return;
        
        try {
            const res = await fetch('/api/admin/users/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: targetUser.id })
            });
            const data = await res.json();
            if (data.success) {
                targetUser.is_verified = true;
                adminStats.verifiedCount++;
                adminStats.unverifiedCount--;
                alert(`已成功驗證 "${targetUser.username}" 的帳號`);
            } else {
                alert("操作失敗: " + data.error);
            }
        } catch (e) {
            alert("連線錯誤");
        }
    };

    const deleteUser = async (targetUser) => {
        if (!confirm(`⚠️ 警告：確定要刪除用戶 "${targetUser.username}"？\n\n此操作無法撤銷！`)) return;
        if (!confirm(`再次確認：刪除 "${targetUser.username}"？`)) return;
        
        try {
            const res = await fetch('/api/admin/users/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: targetUser.id })
            });
            const data = await res.json();
            if (data.success) {
                // 從列表移除
                const idx = adminUsers.value.findIndex(u => u.id === targetUser.id);
                if (idx !== -1) {
                    adminUsers.value.splice(idx, 1);
                }
                // 更新統計
                adminStats.totalUsers--;
                if (targetUser.role === 'admin') adminStats.adminCount--;
                if (targetUser.is_verified) adminStats.verifiedCount--;
                else adminStats.unverifiedCount--;
                
                alert(`已成功刪除用戶 "${targetUser.username}"`);
            } else {
                alert("無法刪除用戶: " + data.error);
            }
        } catch (e) {
            alert("發生錯誤");
        }
    };

    // === 賽季設定 ===
    const showRegulationModal = ref(false);
    const regulationMarks = ref([]);       // [{mark: 'A', is_standard: true}, ...]
    const regulationLoading = ref(false);
    const regulationSaving = ref(false);

    const standardMarks = computed(() =>
        regulationMarks.value.filter(m => m.is_standard).map(m => m.mark)
    );

    const openRegulationModal = async () => {
        showRegulationModal.value = true;
        await loadRegulationSettings();
    };

    const loadRegulationSettings = async () => {
        regulationLoading.value = true;
        try {
            const res = await fetch('/api/admin/regulation-settings');
            const data = await res.json();
            if (data.success) {
                if (data.marks.length > 0) {
                    regulationMarks.value = data.marks;
                } else {
                    // 無資料時建立 A-Z 全列表，標記預設標準
                    const defaults = data.standard_marks || ['F', 'G', 'H', 'I', 'J'];
                    regulationMarks.value = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').map(m => ({
                        mark: m,
                        is_standard: defaults.includes(m)
                    }));
                }
            }
        } catch (e) {
            console.error(e);
        } finally {
            regulationLoading.value = false;
        }
    };

    const toggleRegulationMark = (mark) => {
        const item = regulationMarks.value.find(m => m.mark === mark);
        if (item) item.is_standard = !item.is_standard;
    };

    const saveRegulationSettings = async () => {
        regulationSaving.value = true;
        try {
            const res = await fetch('/api/admin/regulation-settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ standard_marks: standardMarks.value })
            });
            const data = await res.json();
            if (data.success) {
                alert('賽季設定已儲存');
            } else {
                alert('儲存失敗: ' + data.error);
            }
        } catch (e) {
            alert('連線錯誤');
        } finally {
            regulationSaving.value = false;
        }
    };

    // === 編輯用戶 Modal ===
    const showEditUserModal = ref(false);
    const editUserData = reactive({
        id: '',
        username: '',
        email: '',
        password: ''
    });
    const editUserSaving = ref(false);

    // === 編輯用戶方法 ===
    const openEditUserModal = (targetUser) => {
        editUserData.id = targetUser.id;
        editUserData.username = targetUser.username;
        editUserData.email = targetUser.email || '';
        editUserData.password = '';
        showEditUserModal.value = true;
    };

    const submitEditUser = async () => {
        if (!editUserData.username.trim()) {
            alert('請輸入用戶名稱');
            return;
        }
        editUserSaving.value = true;
        try {
            const body = {
                username: editUserData.username.trim(),
                email: editUserData.email.trim()
            };
            if (editUserData.password.trim()) {
                body.password = editUserData.password.trim();
            }
            const res = await fetch(`/api/admin/users/${editUserData.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (data.success) {
                const targetUser = adminUsers.value.find(u => u.id === editUserData.id);
                if (targetUser) {
                    targetUser.username = editUserData.username.trim();
                    targetUser.email = editUserData.email.trim();
                }
                showEditUserModal.value = false;
                alert('用戶資料已更新');
            } else {
                alert('更新失敗: ' + (data.error || '未知錯誤'));
            }
        } catch (e) {
            alert('連線錯誤');
        } finally {
            editUserSaving.value = false;
        }
    };

    // === 推薦列表管理相關 ===
    const showDeckManager = ref(false);
    const adminDecks = ref([]);
    const deckManagerLoading = ref(false);
    const deckManagerSearch = ref('');
    const deckManagerShowAll = ref(false);
    
    // 編輯牌組相關
    const showEditDeckModal = ref(false);
    const editDeckData = reactive({
        id: '',
        name: '',
        is_public: false,
        count: 0
    });
    const editDeckSaving = ref(false);

    // 過濾後的牌組列表
    const filteredAdminDecks = computed(() => {
        if (!deckManagerSearch.value.trim()) return adminDecks.value;
        const query = deckManagerSearch.value.toLowerCase();
        return adminDecks.value.filter(d => 
            d.name.toLowerCase().includes(query) || 
            d.id.toLowerCase().includes(query)
        );
    });

    // 開啟推薦列表管理面板
    const openDeckManager = async () => {
        showDeckManager.value = true;
        deckManagerSearch.value = '';
        await loadAdminDecks();
    };

    // 載入牌組列表
    const loadAdminDecks = async () => {
        deckManagerLoading.value = true;
        try {
            const params = new URLSearchParams();
            if (deckManagerSearch.value.trim()) {
                params.append('q', deckManagerSearch.value.trim());
            }
            if (deckManagerShowAll.value) {
                params.append('all', 'true');
            }
            const res = await fetch(`/api/admin/decks?${params.toString()}`);
            const data = await res.json();
            if (data.success) {
                adminDecks.value = data.decks;
            } else {
                alert("載入失敗: " + (data.error || '權限不足'));
            }
        } catch (e) {
            console.error(e);
            alert("連線錯誤");
        } finally {
            deckManagerLoading.value = false;
        }
    };

    // 開啟編輯牌組彈窗
    const openEditDeckModal = (deck) => {
        editDeckData.id = deck.id;
        editDeckData.name = deck.name;
        editDeckData.is_public = deck.is_public;
        editDeckData.count = deck.count;
        showEditDeckModal.value = true;
    };

    // 儲存編輯
    const saveEditDeck = async () => {
        if (!editDeckData.name.trim()) {
            alert('請輸入牌組名稱');
            return;
        }
        editDeckSaving.value = true;
        try {
            const res = await fetch(`/api/admin/deck/${editDeckData.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: editDeckData.name.trim(),
                    is_public: editDeckData.is_public
                })
            });
            const data = await res.json();
            if (data.success) {
                // 更新本地數據
                const deck = adminDecks.value.find(d => d.id === editDeckData.id);
                if (deck) {
                    deck.name = editDeckData.name.trim();
                    deck.is_public = editDeckData.is_public;
                }
                showEditDeckModal.value = false;
                alert('牌組已更新');
            } else {
                alert("更新失敗: " + data.error);
            }
        } catch (e) {
            alert("連線錯誤");
        } finally {
            editDeckSaving.value = false;
        }
    };

    // 切換公開狀態
    const toggleDeckPublic = async (deck) => {
        const newStatus = !deck.is_public;
        const action = newStatus ? '公開' : '設為私密';
        
        if (!confirm(`確定要將 "${deck.name}" ${action}嗎？`)) return;
        
        try {
            const res = await fetch(`/api/admin/deck/${deck.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_public: newStatus })
            });
            const data = await res.json();
            if (data.success) {
                deck.is_public = newStatus;
                alert(`已將 "${deck.name}" ${action}`);
            } else {
                alert("操作失敗: " + data.error);
            }
        } catch (e) {
            alert("連線錯誤");
        }
    };

    // 刪除牌組
    const deleteAdminDeck = async (deck) => {
        if (!confirm(`⚠️ 警告：確定要刪除牌組 "${deck.name}"？\n\n此操作無法撤銷！`)) return;
        if (!confirm(`再次確認：刪除 "${deck.name}"？`)) return;
        
        try {
            const res = await fetch(`/api/admin/deck/${deck.id}`, {
                method: 'DELETE'
            });
            const data = await res.json();
            if (data.success) {
                // 從列表移除
                const idx = adminDecks.value.findIndex(d => d.id === deck.id);
                if (idx !== -1) {
                    adminDecks.value.splice(idx, 1);
                }
                alert(`已刪除牌組 "${deck.name}"`);
            } else {
                alert("刪除失敗: " + data.error);
            }
        } catch (e) {
            alert("連線錯誤");
        }
    };

    // === 牌組管理（日本/美國/City Leagues） ===
    const showDeckAdmin = ref(false);
    const deckAdminTab = ref('jp'); // jp | us | cl
    const mappingBotCount = ref(5);
    const limitlessIncludeBling = ref(false);
    const limitlessRegionGlobal = ref(true);
    const limitlessRegionJp = ref(true);
    const limitlessMaxTournaments = ref(null);
    const limitlessMaxDecks = ref(null);
    const limitlessManualTournament = ref("567");
    const limitlessManualDeck = ref("27586");
    const limitlessUpdateState = reactive({
        running: false,
        mode: "",
        message: "idle",
        tournaments_found: 0,
        tournaments_done: 0,
        decks_found: 0,
        decks_fetched: 0,
        decks_skipped: 0,
        decks_failed: 0,
        progress: 0,
        elapsed: "",
        logs: []
    });
    let limitlessPollTimer = null;

    const mappingState = reactive({
        running: false,
        total: 0,
        processed: 0,
        matched: 0,
        unmatched: 0,
        errors: 0,
        message: '就緒',
        progress: 0,
        elapsed: '',
        lastResult: null  // 保留上次完成結果
    });
    let mappingPollTimer = null;

    // === 資料庫狀態 ===
    const dbStats = ref(null);

    const loadDbStats = async () => {
        try {
            const res = await fetch('/api/admin/deck-mapping/stats');
            const data = await res.json();
            if (data.success) dbStats.value = data.stats;
        } catch (e) { console.error('Stats error:', e); }
    };

    const openDeckAdmin = () => {
        showDeckAdmin.value = true;
        deckAdminTab.value = 'jp';
        loadDbStats();
        loadDeckUpdateStatus();
    };

    const openLimitlessAdmin = () => {
        showDeckAdmin.value = true;
        deckAdminTab.value = 'cl';
        loadDbStats();
        pollLimitlessUpdate();
    };

    const clearAllDecks = async () => {
        if (!confirm('⚠️ 確定要刪除所有日本牌組資料？\n\n這將清除 imported_decks、deck_cards、id_mapping 三張表。\n此操作無法撤銷！')) return;
        if (!confirm('再次確認：真的要刪除全部嗎？')) return;
        try {
            const res = await fetch('/api/admin/deck-update/clear', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                alert('已清除所有牌組資料');
                loadDbStats();
            } else {
                alert('清除失敗: ' + data.error);
            }
        } catch (e) { alert('連線錯誤'); }
    };

    const startMapping = async () => {
        if (mappingState.running) return;

        const botCount = mappingBotCount.value;
        if (botCount < 1) {
            alert('機器人數量最少為 1');
            return;
        }

        try {
            const res = await fetch('/api/admin/deck-mapping/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bot_count: botCount })
            });
            const data = await res.json();
            if (data.success) {
                mappingState.running = true;
                Object.assign(mappingState, data.status);
                pollMappingStatus();
            } else {
                alert(data.message || '啟動失敗');
            }
        } catch (e) {
            alert('連線錯誤: ' + e.message);
        }
    };

    const pollMappingStatus = async () => {
        if (mappingPollTimer) clearTimeout(mappingPollTimer);
        try {
            const res = await fetch('/api/admin/deck-mapping/status');
            const data = await res.json();
            if (data.success) {
                const st = data.status;
                mappingState.running = st.running;
                mappingState.total = st.total;
                mappingState.processed = st.processed;
                mappingState.matched = st.matched;
                mappingState.unmatched = st.unmatched;
                mappingState.errors = st.errors;
                mappingState.message = st.message;
                mappingState.progress = st.progress;
                mappingState.elapsed = st.elapsed;

                if (st.running) {
                    mappingPollTimer = setTimeout(pollMappingStatus, 2000);
                } else {
                    mappingState.lastResult = {
                        processed: st.processed,
                        matched: st.matched,
                        unmatched: st.unmatched,
                        elapsed: st.elapsed
                    };
                }
            }
        } catch (e) {
            console.error('Mapping poll error:', e);
            mappingPollTimer = setTimeout(pollMappingStatus, 3000);
        }
    };

    // === 效果角色標籤 ===
    const cardRoleState = reactive({
        running: false, progress: 0, message: '就緒', logs: [],
        total: 0, processed: 0, labeled: 0, auto_approved: 0, pending_added: 0,
        rejected_roles: 0, failed: 0,
    });
    const cardRoleQueue = ref({ pending: 0, approved: 0, rejected: 0 });
    const cardRoleBatchSize = ref(200);
    const cardRoleRoleOptions = ['draw', 'discard', 'search', 'ramp', 'evolve_accel', 'heal', 'switch', 'damage', 'condition', 'stall'];
    const cardRoleFilterStatus = ref('pending');
    const cardRoleFilterRole = ref('');
    const cardRoleList = ref([]);
    const cardRoleListLoading = ref(false);
    const cardRoleTotal = ref(0);
    const cardRolePage = ref(1);
    const cardRolePageSize = ref(20);
    const cardRoleSelected = ref(new Set());
    let cardRolePollTimer = null;

    const cardRoleAllSelected = computed(
        () => cardRoleList.value.length > 0 && cardRoleList.value.filter(t => t.status === 'pending').every(t => cardRoleSelected.value.has(t.id))
    );

    const openCardRolesTab = () => {
        deckAdminTab.value = 'roles';
        loadCardRoleStatus();
        loadCardRoleList();
    };

    const loadCardRoleStatus = async () => {
        try {
            const res = await fetch('/api/admin/card-roles/status');
            const data = await res.json();
            if (!data.success) return;
            const st = data.status || {};
            cardRoleState.running = !!st.running;
            cardRoleState.progress = st.progress || 0;
            cardRoleState.message = st.message || '';
            cardRoleState.logs = st.logs || [];
            cardRoleState.total = st.total || 0;
            cardRoleState.processed = st.processed || 0;
            cardRoleState.labeled = st.labeled || 0;
            cardRoleState.auto_approved = st.auto_approved || 0;
            cardRoleState.pending_added = st.pending_added || 0;
            cardRoleState.rejected_roles = st.rejected_roles || 0;
            cardRoleState.failed = st.failed || 0;
            cardRoleQueue.value = data.queue || { pending: 0, approved: 0, rejected: 0 };
        } catch (e) { console.error('Card role status error:', e); }
    };

    const startCardRoleLabeling = async () => {
        if (cardRoleState.running) return;
        const limit = Math.max(1, cardRoleBatchSize.value || 200);
        try {
            const res = await fetch('/api/admin/card-roles/label', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ limit })
            });
            const data = await res.json();
            if (data.success) {
                cardRoleState.running = true;
                Object.assign(cardRoleState, data.status || {});
                cardRoleState.running = true;
                pollCardRoleStatus();
                loadCardRoleList();
            } else {
                alert(data.message || '啟動失敗');
            }
        } catch (e) { alert('連線錯誤: ' + e.message); }
    };

    const pollCardRoleStatus = async () => {
        if (cardRolePollTimer) clearTimeout(cardRolePollTimer);
        try {
            const res = await fetch('/api/admin/card-roles/status');
            const data = await res.json();
            if (data.success) {
                const st = data.status || {};
                Object.assign(cardRoleState, {
                    running: !!st.running,
                    progress: st.progress || 0,
                    message: st.message || '',
                    logs: st.logs || [],
                    total: st.total || 0,
                    processed: st.processed || 0,
                    labeled: st.labeled || 0,
                    auto_approved: st.auto_approved || 0,
                    pending_added: st.pending_added || 0,
                    rejected_roles: st.rejected_roles || 0,
                    failed: st.failed || 0,
                });
                cardRoleQueue.value = data.queue || cardRoleQueue.value;
                if (st.running) {
                    cardRolePollTimer = setTimeout(pollCardRoleStatus, 2500);
                } else {
                    loadCardRoleStatus();
                    loadCardRoleList();
                }
            }
        } catch (e) {
            console.error('Card role poll error:', e);
            cardRolePollTimer = setTimeout(pollCardRoleStatus, 4000);
        }
    };

    const loadCardRoleList = async () => {
        cardRoleListLoading.value = true;
        try {
            const params = new URLSearchParams({
                status: cardRoleFilterStatus.value === 'all' ? '' : cardRoleFilterStatus.value,
                role: cardRoleFilterRole.value,
                page: cardRolePage.value,
                page_size: cardRolePageSize.value,
            });
            const res = await fetch('/api/admin/card-roles/list?' + params.toString());
            const data = await res.json();
            if (data.success) {
                cardRoleList.value = data.tags || [];
                cardRoleTotal.value = data.total || 0;
                // 清除已不在本頁的選取
                const valid = new Set((data.tags || []).map(t => t.id));
                cardRoleSelected.value = new Set([...cardRoleSelected.value].filter(id => valid.has(id)));
            }
        } catch (e) { console.error('Card role list error:', e); }
        finally { cardRoleListLoading.value = false; }
    };

    const toggleCardRoleSelect = (id) => {
        const next = new Set(cardRoleSelected.value);
        if (next.has(id)) next.delete(id); else next.add(id);
        cardRoleSelected.value = next;
    };

    const toggleCardRoleSelectAll = () => {
        if (cardRoleAllSelected.value) {
            cardRoleSelected.value = new Set();
        } else {
            cardRoleSelected.value = new Set(cardRoleList.value.filter(t => t.status === 'pending').map(t => t.id));
        }
    };

    const reviewCardRoles = async (action, ids = null) => {
        const idList = ids || [...cardRoleSelected.value];
        if (!idList.length) {
            if (!ids) alert('請先勾選要操作的標註');
            return;
        }
        try {
            const res = await fetch('/api/admin/card-roles/review', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: idList, action })
            });
            const data = await res.json();
            if (data.success) {
                alert(`已${action === 'approve' ? '批准' : '拒絕'} ${data.updated} 筆標註`);
                if (!ids) cardRoleSelected.value = new Set();
                loadCardRoleList();
                loadCardRoleStatus();
            } else {
                alert('操作失敗: ' + (data.error || ''));
            }
        } catch (e) { alert('連線錯誤: ' + e.message); }
    };

    const clearCardRoleQueue = async (status) => {
        const label = status === 'pending' ? '待審核' : '已拒絕';
        if (!confirm(`⚠️ 確定要清空所有「${label}」狀態的標註？\n此操作無法復原！`)) return;
        try {
            const res = await fetch('/api/admin/card-roles/clear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status })
            });
            const data = await res.json();
            if (data.success) {
                alert(`已清空 ${data.deleted} 筆`);
                loadCardRoleList();
                loadCardRoleStatus();
            } else {
                alert('清空失敗: ' + (data.error || ''));
            }
        } catch (e) { alert('連線錯誤'); }
    };

    const roleBadgeClass = (role) => {
        const colors = {
            draw: 'bg-blue-900/50 text-blue-400',
            discard: 'bg-red-900/50 text-red-400',
            search: 'bg-green-900/50 text-green-400',
            ramp: 'bg-orange-900/50 text-orange-400',
            evolve_accel: 'bg-pink-900/50 text-pink-400',
            heal: 'bg-teal-900/50 text-teal-400',
            switch: 'bg-indigo-900/50 text-indigo-400',
            damage: 'bg-yellow-900/50 text-yellow-400',
            condition: 'bg-fuchsia-900/50 text-fuchsia-400',
            stall: 'bg-cyan-900/50 text-cyan-400',
        };
        return colors[role] || 'bg-gray-900/50 text-gray-400';
    };

    const formatCardRoleParams = (params) => {
        try {
            const obj = (params && typeof params === 'object') ? params : JSON.parse(params || '{}');
            return Object.entries(obj).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(', ') || '{}';
        } catch (e) { return String(params || '{}'); }
    };

    const highlightCardRoleEvidence = (text, evidence) => {
        if (!text || !evidence) return escapeHtml(text || '');
        const index = text.indexOf(evidence);
        if (index < 0) return escapeHtml(text);
        const before = escapeHtml(text.slice(0, index));
        const hit = escapeHtml(text.slice(index, index + evidence.length));
        const after = escapeHtml(text.slice(index + evidence.length));
        return before + '<mark class="bg-yellow-500/40 text-yellow-100 px-0.5 rounded font-bold">' + hit + '</mark>' + after;
    };

    const escapeHtml = (value) => {
        return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    };

    // === 每日牌組更新 ===
    const dailyUpdateBotCount = ref(3);
    const dailyUpdateState = reactive({
        running: false,
        total_pages: 0,
        pages_done: 0,
        decks_found: 0,
        decks_new: 0,
        decks_skipped: 0,
        decks_failed: 0,
        message: '就緒',
        progress: 0,
        elapsed: '',
        lastResult: null,
        last_run: null,
        next_run: null,
        gap_fill: {},
    });
    let dailyPollTimer = null;

    const startDailyUpdate = async () => {
        if (dailyUpdateState.running) return;
        const botCount = dailyUpdateBotCount.value;
        if (botCount < 1) { alert('機器人數量最少為 1'); return; }
        try {
            const res = await fetch('/api/admin/deck-update/daily', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bot_count: botCount })
            });
            const data = await res.json();
            if (data.success) {
                dailyUpdateState.running = true;
                Object.assign(dailyUpdateState, data.status);
                pollDailyUpdate();
            } else {
                alert(data.message || '啟動失敗');
            }
        } catch (e) { alert('連線錯誤'); }
    };

    const pollDailyUpdate = async () => {
        if (dailyPollTimer) clearTimeout(dailyPollTimer);
        try {
            const res = await fetch('/api/admin/deck-update/status');
            const data = await res.json();
            if (data.success) {
                const st = data.status;
                Object.assign(dailyUpdateState, st);
                if (st.running) {
                    dailyPollTimer = setTimeout(pollDailyUpdate, 2000);
                } else {
                    dailyUpdateState.lastResult = { new: st.decks_new, skipped: st.decks_skipped, elapsed: st.elapsed };
                }
            }
        } catch (e) {
            dailyPollTimer = setTimeout(pollDailyUpdate, 3000);
        }
    };

    // 載入牌庫更新狀態（開啟面板時呼叫一次；進行中則接續輪詢）
    const loadDeckUpdateStatus = async () => {
        try {
            const res = await fetch('/api/admin/deck-update/status');
            const data = await res.json();
            if (data.success) {
                Object.assign(dailyUpdateState, data.status);
                if (data.status.running) pollDailyUpdate();
            }
        } catch (e) { console.error('deck update status load error:', e); }
    };

    // 手動觸發缺漏偵測（輪轉增量，預設掃 10 頁，補齊從未匯入的牌組）
    const startGapFill = async () => {
        if (dailyUpdateState.running) return;
        try {
            const res = await fetch('/api/admin/deck-update/gap-fill', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            const data = await res.json();
            if (data.success) {
                Object.assign(dailyUpdateState, data.status);
                pollDailyUpdate();
            } else {
                alert(data.message || '啟動失敗');
            }
        } catch (e) { alert('連線錯誤'); }
    };

    // === 完整牌組更新 ===
    const fullUpdateBotCount = ref(5);
    const fullUpdateState = reactive({
        running: false,
        total_pages: 0,
        pages_done: 0,
        decks_found: 0,
        decks_new: 0,
        decks_skipped: 0,
        decks_failed: 0,
        cards_total: 0,
        message: '就緒',
        progress: 0,
        elapsed: '',
        lastResult: null
    });
    let fullPollTimer = null;

    const startFullUpdate = async () => {
        if (fullUpdateState.running) return;
        const botCount = fullUpdateBotCount.value;
        if (botCount < 1) { alert('機器人數量最少為 1'); return; }
        try {
            const res = await fetch('/api/admin/deck-update/full', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bot_count: botCount })
            });
            const data = await res.json();
            if (data.success) {
                fullUpdateState.running = true;
                Object.assign(fullUpdateState, data.status);
                pollFullUpdate();
            } else {
                alert(data.message || '啟動失敗');
            }
        } catch (e) { alert('連線錯誤'); }
    };

    const pollFullUpdate = async () => {
        if (fullPollTimer) clearTimeout(fullPollTimer);
        try {
            const res = await fetch('/api/admin/deck-update/status');
            const data = await res.json();
            if (data.success) {
                const st = data.status;
                Object.assign(fullUpdateState, st);
                if (st.running) {
                    fullPollTimer = setTimeout(pollFullUpdate, 2000);
                } else {
                    fullUpdateState.lastResult = { new: st.decks_new, skipped: st.decks_skipped, elapsed: st.elapsed };
                }
            }
        } catch (e) {
            fullPollTimer = setTimeout(pollFullUpdate, 3000);
        }
    };

    const pollLimitlessUpdate = async () => {
        if (limitlessPollTimer) clearTimeout(limitlessPollTimer);
        try {
            const res = await fetch('/api/admin/limitless/update/status');
            const data = await res.json();
            if (data.success) {
                Object.assign(limitlessUpdateState, data.status);
                if (data.status.running) {
                    limitlessPollTimer = setTimeout(pollLimitlessUpdate, 2000);
                }
            }
        } catch (e) {
            limitlessPollTimer = setTimeout(pollLimitlessUpdate, 3000);
        }
    };

    const startLimitlessUpdate = async () => {
        if (limitlessUpdateState.running) return;
        const regions = [];
        if (limitlessRegionGlobal.value) regions.push('global');
        if (limitlessRegionJp.value) regions.push('jp');
        if (regions.length === 0) return alert('Select at least one Limitless region');
        try {
            const body = {
                include_bling: limitlessIncludeBling.value,
                regions,
                stale_hours: 24,
                max_tournaments: limitlessMaxTournaments.value || null,
                max_decks: limitlessMaxDecks.value || null
            };
            const res = await fetch('/api/admin/limitless/update/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (data.success) {
                Object.assign(limitlessUpdateState, data.status);
                pollLimitlessUpdate();
            } else {
                alert(data.message || 'Limitless update failed to start');
            }
        } catch (e) {
            alert('Limitless update request failed');
        }
    };

    const updateLimitlessTournament = async () => {
        const tid = limitlessManualTournament.value.trim();
        if (!tid) return;
        try {
            const res = await fetch(`/api/admin/limitless/update/tournament/${encodeURIComponent(tid)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ include_bling: limitlessIncludeBling.value, stale_hours: 0, max_decks: limitlessMaxDecks.value || null })
            });
            const data = await res.json();
            if (data.success) {
                alert('Tournament update finished');
                pollLimitlessUpdate();
            } else {
                alert(data.error || 'Tournament update failed');
            }
        } catch (e) {
            alert('Tournament update request failed');
        }
    };

    const updateLimitlessDeck = async () => {
        const did = limitlessManualDeck.value.trim();
        if (!did) return;
        try {
            const res = await fetch(`/api/admin/limitless/update/deck/${encodeURIComponent(did)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ include_bling: limitlessIncludeBling.value })
            });
            const data = await res.json();
            if (data.success) {
                alert('Deck update finished');
                pollLimitlessUpdate();
            } else {
                alert(data.error || 'Deck update failed');
            }
        } catch (e) {
            alert('Deck update request failed');
        }
    };

        return {
        // 卡牌更新
        showUpdateModal,
        uiState,
        versionInfo,
        customUrl,
        updateState,
        
        expansionList,
        expansionGrouped,
        selectedExpansions,
        selectedRegulations,
        updateJapanese, 
        skipImages, // [新增]
        loadingExpansions,
        fetchExpansions, 

        openUpdateModal,
        checkVersion,
        startUpdate,
        
        // 用戶管理
        showAdminPanel,
        adminUsers,
        adminUsersLoading,
        adminUserSearch,
        adminStats,
        filteredAdminUsers,
        formatDate,
        openAdminPanel,
        loadAdminUsers,
        filterAdminUsers,
        toggleUserRole,
        verifyUser,
        deleteUser,

        // 賽季設定
        showRegulationModal, regulationMarks, regulationLoading, regulationSaving,
        standardMarks, openRegulationModal, loadRegulationSettings,
        toggleRegulationMark, saveRegulationSettings,

        // 編輯用戶
        showEditUserModal, editUserData, editUserSaving,
        openEditUserModal, submitEditUser,

        // 推薦列表管理
        showDeckManager,
        adminDecks,
        deckManagerLoading,
        deckManagerSearch,
        deckManagerShowAll,
        filteredAdminDecks,
        showEditDeckModal,
        editDeckData,
        editDeckSaving,
        openDeckManager,
        loadAdminDecks,
        openEditDeckModal,
        saveEditDeck,
        toggleDeckPublic,
        deleteAdminDeck,

        // 牌組管理（日本/美國/City Leagues）
        showDeckAdmin,
        deckAdminTab,
        dbStats,
        loadDbStats,
        clearAllDecks,
        mappingBotCount,
        mappingState,
        openDeckAdmin,
        openLimitlessAdmin,
        startMapping,
        pollMappingStatus,

        // 效果角色標籤
        cardRoleState,
        cardRoleQueue,
        cardRoleBatchSize,
        cardRoleRoleOptions,
        cardRoleFilterStatus,
        cardRoleFilterRole,
        cardRoleList,
        cardRoleListLoading,
        cardRoleTotal,
        cardRolePage,
        cardRolePageSize,
        cardRoleSelected,
        cardRoleAllSelected,
        openCardRolesTab,
        loadCardRoleStatus,
        startCardRoleLabeling,
        pollCardRoleStatus,
        loadCardRoleList,
        toggleCardRoleSelect,
        toggleCardRoleSelectAll,
        reviewCardRoles,
        clearCardRoleQueue,
        roleBadgeClass,
        formatCardRoleParams,
        highlightCardRoleEvidence,

        // 每日 + 完整牌組更新
        dailyUpdateBotCount,
        dailyUpdateState,
        startDailyUpdate,
        loadDeckUpdateStatus,
        startGapFill,
        fullUpdateBotCount,
        fullUpdateState,
        startFullUpdate,
        limitlessIncludeBling,
        limitlessRegionGlobal,
        limitlessRegionJp,
        limitlessMaxTournaments,
        limitlessMaxDecks,
        limitlessManualTournament,
        limitlessManualDeck,
        limitlessUpdateState,
        startLimitlessUpdate,
        pollLimitlessUpdate,
        updateLimitlessTournament,
        updateLimitlessDeck,

        // ========== JP 卡牌更新 (Limitless) ==========
        jpUpdateState,
        jpTestSetCode,
        jpTestNumber,
        jpTestResult,
        jpTestCard,
        jpExpansionList,
        jpSelectedExpansions,
        jpLoadingExpansions,
        jpWorkers,
        jpDelay,
        openJPUpdateModal,
        fetchJPExpansions,
        toggleSelectAllJP,
        startJPSingleSet,
        startJPSelected,
        startJPAll,
        pollJPUpdateStatus,
        showJPUpdateModal
    };
}
