// /Pokemon/public/js/io_manager.js

function useIOManager(deck, addToDeck, currentDeckName, workspaceAPI = null) {
    const { ref } = Vue;

    const showImportModal = ref(false);
    const importText = ref("");
    const isImporting = ref(false);
    const importStatus = ref("");
    const conflictQueue = ref([]);
    const notFoundList = ref([]);
    const importIntoNewTab = ref(true);
    const importBuffer = ref([]);

    const showExportModal = ref(false);
    const exportTextContent = ref("");
    const exportWithId = ref(false);

    const showPrintModal = ref(false);
    const generating = ref(false);
    const currentGenPage = ref(0);
    const totalGenPages = ref(0);

    const showSaveModal = ref(false);
    const saveDeckName = ref("");
    const saveIsPublic = ref(false);
    const isSaving = ref(false);
    const showShareModal = ref(false);
    const savedDeckId = ref("");
    const shareUrl = ref("");

    const showDeckLibrary = ref(false);
    const publicDecks = ref([]);
    const publicDecksLoading = ref(false);
    const publicDeckSearchQuery = ref("");

    const showJpDeckLibrary = ref(false);
    const jpDecks = ref([]);
    const jpDecksLoading = ref(false);
    const jpDeckSearchQuery = ref("");
    const jpDeckSortMode = ref("match_count");
    const jpDeckPage = ref(1);
    const jpDeckTotalPages = ref(1);
    const jpSuggestion = ref(null);
    const jpImportMissing = ref([]);
    const showJpPreviewModal = ref(false);
    const currentJpPreviewImage = ref("");

    const showLimitlessDeckLibrary = ref(false);
    const limitlessTournaments = ref([]);
    const limitlessTournamentsLoading = ref(false);
    const selectedLimitlessTournament = ref(null);
    const limitlessDecks = ref([]);
    const limitlessDecksLoading = ref(false);
    const limitlessDeckSearchQuery = ref("");
    const limitlessDeckSortMode = ref("date");
    const limitlessDeckRegion = ref("");
    const limitlessDeckFormat = ref("");
    const limitlessDeckPage = ref(1);
    const limitlessDeckTotalPages = ref(1);
    const limitlessTournamentPage = ref(1);
    const limitlessTournamentTotalPages = ref(1);
    const selectedLimitlessDeck = ref(null);
    const limitlessDetailLoading = ref(false);
    const limitlessCardsLoading = ref(false);
    const limitlessLang = ref("tw");
    const limitlessMode = ref("normal");
    const limitlessImporting = ref(false);
    const limitlessImportMissing = ref([]);
    const importMissingNotice = ref(null);

    const showLiveModal = ref(false);
    const liveDeckString = ref("");
    const liveNotFound = ref([]);
    const isConverting = ref(false);

    const normalizeMissingImportCard = (item = {}) => {
        const source = item || {};
        const count = Number(source.count || source.quantity || 1);
        return {
            name: source.jp_name || source.name_jp || source.name_tw || source.card_name || source.name || source.variant_id || "Unknown card",
            count: Number.isFinite(count) && count > 0 ? count : 1,
            code: source.jp_code || [source.set_name, source.set_no].filter(Boolean).join(" ") || source.variant_id || source.local_card_id || "",
            reason: source.reason || source.match_error || "中文牌庫中無法找到此卡牌",
            image_url: source.limitless_image_url || source.image_url || source.image || ""
        };
    };

    const openImportMissingNotice = ({ source = "import", title = "牌組導入", importedCount = 0, missing = [] } = {}) => {
        importMissingNotice.value = {
            source,
            title,
            importedCount: Number(importedCount || 0),
            missing: (missing || []).map(normalizeMissingImportCard)
        };
    };

    const closeImportMissingNotice = () => {
        importMissingNotice.value = null;
    };

    const makeUniqueDeck = (cards) => (cards || []).map(card => ({
        ...card,
        uniqueId: Date.now() + Math.random().toString(36).substr(2, 9)
    }));

    const openImportedDeckTab = ({ source = "scratch", title = "未命名牌組", cards = [], meta = {} } = {}) => {
        if (workspaceAPI && typeof workspaceAPI.openImportedDeckTab === "function") {
            workspaceAPI.openImportedDeckTab({ source, title, cards, meta });
            return true;
        }
        return false;
    };

    const replaceCurrentDeckFallback = (cards, title, actionName) => {
        const run = () => {
            deck.value.length = 0;
            deck.value.push(...makeUniqueDeck(cards));
        };
        if (workspaceAPI && typeof workspaceAPI.withTimelineBatch === "function") {
            workspaceAPI.withTimelineBatch(actionName, run);
        } else {
            run();
            if (workspaceAPI && typeof workspaceAPI.markTimelineAction === "function") {
                workspaceAPI.markTimelineAction(actionName);
            }
        }
        currentDeckName.value = title || "未命名牌組";
    };

    const openDeckInTab = (payload, fallbackAction) => {
        if (!openImportedDeckTab(payload)) {
            replaceCurrentDeckFallback(payload.cards || [], payload.title, fallbackAction);
        }
    };

    const openJpPreview = (imgUrl) => {
        if (!imgUrl) return;
        currentJpPreviewImage.value = imgUrl;
        showJpPreviewModal.value = true;
    };

    const searchTag = (tagName) => {
        jpDeckSearchQuery.value = String(tagName || "").split(":")[0];
        jpDeckPage.value = 1;
        searchJpDecks(1);
    };

    const openLimitlessDeckLibrary = async () => {
        showLimitlessDeckLibrary.value = true;
        if (limitlessTournaments.value.length === 0) await searchLimitlessTournaments(1);
    };

    const searchLimitlessTournaments = async (page = 1) => {
        limitlessTournamentsLoading.value = true;
        try {
            const params = new URLSearchParams({
                q: limitlessDeckSearchQuery.value.trim(),
                page,
                region: limitlessDeckRegion.value,
                format: limitlessDeckFormat.value
            });
            const res = await fetch(`/api/limitless-tournaments/list?${params.toString()}`);
            const data = await res.json();
            if (data.success) {
                limitlessTournaments.value = data.tournaments || [];
                limitlessTournamentPage.value = data.page || 1;
                limitlessTournamentTotalPages.value = data.pages || 1;
                if (limitlessTournaments.value.length && !selectedLimitlessTournament.value) {
                    await openLimitlessTournament(limitlessTournaments.value[0].tournament_id);
                } else if (limitlessTournaments.value.length === 0) {
                    selectedLimitlessTournament.value = null;
                    limitlessDecks.value = [];
                    selectedLimitlessDeck.value = null;
                }
            }
        } catch (e) {
            console.error(e);
        } finally {
            limitlessTournamentsLoading.value = false;
        }
    };

    const openLimitlessTournament = async (tournamentId) => {
        limitlessDecksLoading.value = true;
        selectedLimitlessDeck.value = null;
        try {
            const params = new URLSearchParams({ q: limitlessDeckSearchQuery.value.trim() });
            const res = await fetch(`/api/limitless-tournaments/${encodeURIComponent(tournamentId)}/decks?${params.toString()}`);
            const data = await res.json();
            if (data.success) {
                selectedLimitlessTournament.value = data.tournament;
                limitlessDecks.value = data.decks || [];
                if (limitlessDecks.value.length) await openLimitlessDeck(limitlessDecks.value[0].deck_id);
            }
        } catch (e) {
            console.error(e);
        } finally {
            limitlessDecksLoading.value = false;
        }
    };

    const searchLimitlessDecks = (page = 1) => searchLimitlessTournaments(page);

    const openLimitlessDeck = async (deckId) => {
        limitlessDetailLoading.value = true;
        selectedLimitlessDeck.value = null;
        try {
            const res = await fetch(`/api/limitless-decks/${encodeURIComponent(deckId)}`);
            const data = await res.json();
            if (data.success) {
                selectedLimitlessDeck.value = data;
                if (!selectedLimitlessDeck.value.cards) selectedLimitlessDeck.value.cards = { tw: {}, jp: {}, en: {} };
                const available = data.available || {};
                limitlessLang.value = available.tw && available.tw.normal ? "tw" : (available.jp && available.jp.normal ? "jp" : "en");
                limitlessMode.value = "normal";
                limitlessImportMissing.value = [];
                await loadLimitlessCards();
            } else {
                alert(data.error || "Limitless deck not found");
            }
        } catch (e) {
            console.error(e);
            alert("Limitless deck load failed");
        } finally {
            limitlessDetailLoading.value = false;
        }
    };

    const closeLimitlessDeckDetail = () => {
        selectedLimitlessDeck.value = null;
    };

    const loadLimitlessCards = async () => {
        const detail = selectedLimitlessDeck.value;
        if (!detail || !detail.deck) return;
        if (!detail.cards) detail.cards = { tw: {}, jp: {}, en: {} };
        if (!detail.cards[limitlessLang.value]) detail.cards[limitlessLang.value] = {};
        if (detail.cards[limitlessLang.value][limitlessMode.value]) return;
        limitlessCardsLoading.value = true;
        try {
            const params = new URLSearchParams({ language: limitlessLang.value, mode: limitlessMode.value });
            const res = await fetch(`/api/limitless-decks/${encodeURIComponent(detail.deck.deck_id)}/cards?${params.toString()}`);
            const data = await res.json();
            if (data.success) detail.cards[limitlessLang.value][limitlessMode.value] = data.cards || [];
        } catch (e) {
            console.error(e);
        } finally {
            limitlessCardsLoading.value = false;
        }
    };

    const setLimitlessLang = async (lang) => {
        limitlessLang.value = lang;
        await loadLimitlessCards();
    };

    const setLimitlessMode = async (mode) => {
        limitlessMode.value = mode;
        await loadLimitlessCards();
    };

    Vue.watch([limitlessLang, limitlessMode], () => {
        loadLimitlessCards();
    });

    const getLimitlessCards = () => {
        const detail = selectedLimitlessDeck.value;
        if (!detail || !detail.cards) return [];
        const langBucket = detail.cards[limitlessLang.value] || {};
        return langBucket[limitlessMode.value] || [];
    };

    const getLimitlessSectionCards = (section) => getLimitlessCards().filter(c => c.section === section);
    const getLimitlessSectionCount = (section) => getLimitlessSectionCards(section).reduce((sum, c) => sum + Number(c.count || 0), 0);
    const getLimitlessDeckName = (item) => {
        if (!item) return "";
        return item.archetype_zh || item.title_zh || item.archetype || item.title || "未命名牌組";
    };
    const getLimitlessTagName = (item, tag, index) => {
        const translated = item && Array.isArray(item.tags_zh) ? item.tags_zh[index] : "";
        return translated || tag;
    };
    const getLimitlessSectionName = (section) => ({
        pokemon: "Pokemon",
        trainer: "Trainer",
        energy: "Energy",
        unknown: "Other"
    }[section] || section);
    const prevLimitlessPage = () => { if (limitlessTournamentPage.value > 1) searchLimitlessTournaments(limitlessTournamentPage.value - 1); };
    const nextLimitlessPage = () => { if (limitlessTournamentPage.value < limitlessTournamentTotalPages.value) searchLimitlessTournaments(limitlessTournamentPage.value + 1); };

    const importLimitlessDeck = async () => {
        if (!selectedLimitlessDeck.value || limitlessImporting.value) return;
        limitlessImporting.value = true;
        limitlessImportMissing.value = [];
        try {
            const res = await fetch(`/api/limitless-decks/${encodeURIComponent(selectedLimitlessDeck.value.deck.deck_id)}/import`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ language: "tw", mode: limitlessMode.value })
            });
            const data = await res.json();
            if (data.success) {
                const title = data.name || selectedLimitlessDeck.value.deck.title || "LimitLess Deck";
                openDeckInTab({
                    source: "limitless",
                    title,
                    cards: data.deck || [],
                    meta: {
                        deck_id: selectedLimitlessDeck.value.deck.deck_id,
                        tournament: selectedLimitlessDeck.value.deck.tournament_title,
                        player: selectedLimitlessDeck.value.deck.player_name
                    }
                }, "匯入 LimitLess 牌組");
                limitlessImportMissing.value = data.missing || [];
                if (limitlessImportMissing.value.length) {
                    openImportMissingNotice({
                        source: "limitless",
                        title,
                        importedCount: data.imported_count || (data.deck || []).length,
                        missing: limitlessImportMissing.value
                    });
                } else {
                    showLimitlessDeckLibrary.value = false;
                }
            } else {
                alert(data.error || "Limitless deck import failed");
            }
        } catch (e) {
            console.error(e);
            alert("Limitless deck import request failed");
        } finally {
            limitlessImporting.value = false;
        }
    };

    const openImportModal = () => {
        importText.value = "";
        conflictQueue.value = [];
        notFoundList.value = [];
        importBuffer.value = [];
        importIntoNewTab.value = true;
        showImportModal.value = true;
    };

    const addImportedTextCard = (card) => {
        if (importIntoNewTab.value) {
            importBuffer.value.push({ ...card });
            return true;
        }
        return addToDeck(card);
    };

    const finishTextImport = () => {
        if (importIntoNewTab.value && importBuffer.value.length) {
            const title = `文字匯入 ${new Date().toLocaleDateString("zh-TW")}`;
            openDeckInTab({
                source: "scratch",
                title,
                cards: importBuffer.value,
                meta: { source: "text-import" }
            }, "文字匯入牌組");
        } else if (workspaceAPI && typeof workspaceAPI.markTimelineAction === "function") {
            workspaceAPI.markTimelineAction("文字匯入牌組");
        }
        importBuffer.value = [];
        showImportModal.value = false;
    };

    const startImport = async () => {
        if (!importText.value.trim()) return;
        isImporting.value = true;
        conflictQueue.value = [];
        notFoundList.value = [];
        importBuffer.value = [];
        const lines = importText.value.split("\n").filter(line => line.trim() !== "");
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            importStatus.value = `搜尋: ${line}`;
            const match = line.match(/^(.+?)(?:\s+\(([a-zA-Z0-9_-]+)\))?\s+(\d+)(?:\s*張?)?$/) || [null, line, null, "1"];
            const name = match[1].trim();
            const id = match[2] ? match[2].trim() : null;
            const count = parseInt(match[3]);

            try {
                let results = [];
                if (id) {
                    const resId = await fetch(`/api/search?q=${encodeURIComponent(id)}`);
                    results = await resId.json();
                }
                if (results.length === 0) {
                    const resName = await fetch(`/api/search?q=${encodeURIComponent(name)}`);
                    results = await resName.json();
                }
                if (results.length === 0) {
                    notFoundList.value.push(name);
                } else if (results.length === 1) {
                    for (let c = 0; c < count; c++) addImportedTextCard(results[0]);
                } else {
                    if (id) {
                        const exactMatch = results.find(c => c.image_file && c.image_file.includes(id));
                        if (exactMatch) {
                            for (let c = 0; c < count; c++) addImportedTextCard(exactMatch);
                            continue;
                        }
                    }
                    conflictQueue.value.push({ name, count, matches: results });
                }
            } catch (e) {
                notFoundList.value.push(`${name} (error)`);
            }
            await new Promise(r => setTimeout(r, 20));
        }
        isImporting.value = false;
        if (conflictQueue.value.length === 0) finishTextImport();
    };

    const resolveConflict = (item, card) => {
        for (let i = 0; i < item.count; i++) addImportedTextCard(card);
        conflictQueue.value.shift();
        if (conflictQueue.value.length === 0) finishTextImport();
    };

    const skipConflict = () => {
        conflictQueue.value.shift();
        if (conflictQueue.value.length === 0) finishTextImport();
    };

    const openExportModal = () => {
        regenerateExportText();
        showExportModal.value = true;
    };

    const regenerateExportText = () => {
        const counts = {};
        deck.value.forEach(c => {
            const id = c.image_file ? c.image_file.replace(/\.[^/.]+$/, "") : "";
            const key = exportWithId.value ? `${c.name} (${id})` : c.name;
            counts[key] = (counts[key] || 0) + 1;
        });
        exportTextContent.value = Object.entries(counts).map(([k, v]) => `${k} ${v}張`).join("\n");
    };

    const copyExportText = () => navigator.clipboard.writeText(exportTextContent.value).then(() => alert("已複製"));

    const openSaveModal = () => {
        if (deck.value.length === 0) return alert("牌組是空的");
        saveDeckName.value = currentDeckName.value || saveDeckName.value || "新牌組";
        showSaveModal.value = true;
    };

    const submitSaveDeck = async () => {
        if (!saveDeckName.value.trim()) return alert("請輸入牌組名稱");
        isSaving.value = true;
        try {
            const res = await fetch("/api/deck/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: saveDeckName.value, deck: deck.value, is_public: saveIsPublic.value })
            });
            const data = await res.json();
            if (data.success) {
                currentDeckName.value = saveDeckName.value;
                savedDeckId.value = data.id;
                shareUrl.value = `${window.location.origin}/card/${data.id}`;
                showSaveModal.value = false;
                showShareModal.value = true;
            } else {
                alert("儲存失敗: " + data.error);
            }
        } catch (e) {
            alert("連線失敗");
        } finally {
            isSaving.value = false;
        }
    };

    const copyShareLink = () => navigator.clipboard.writeText(shareUrl.value).then(() => alert("已複製"));

    const openDeckLibrary = async () => {
        showDeckLibrary.value = true;
        publicDeckSearchQuery.value = "";
        await searchPublicDecks();
    };

    const searchPublicDecks = async () => {
        publicDecksLoading.value = true;
        try {
            const q = encodeURIComponent(publicDeckSearchQuery.value.trim());
            const res = await fetch(`/api/decks/public?q=${q}`);
            publicDecks.value = await res.json();
        } catch (e) {
            console.error(e);
        } finally {
            publicDecksLoading.value = false;
        }
    };

    const loadPublicDeck = async (id, loadCallback = null) => {
        try {
            const res = await fetch(`/api/deck/${id}`);
            const data = await res.json();
            if (data.success) {
                if (loadCallback) {
                    loadCallback(data.deck, data.name);
                } else {
                    openDeckInTab({
                        source: "public",
                        title: data.name || "公開牌組",
                        cards: data.deck || [],
                        meta: { deck_id: id }
                    }, "載入公開牌組");
                }
                showDeckLibrary.value = false;
            } else {
                alert(data.error || "牌組載入失敗");
            }
        } catch (e) {
            alert("牌組載入失敗");
        }
    };

    const openPrintModal = () => {
        if (deck.value.length === 0) return alert("牌組是空的");
        showPrintModal.value = true;
    };

    const generateDeckImage = async (format) => {
        generating.value = true;
        const isA4 = format === "A4";
        const cardsPerPage = isA4 ? 9 : 18;
        const cols = isA4 ? 3 : 6;
        const totalCards = deck.value.length;
        const pages = Math.ceil(totalCards / cardsPerPage);
        totalGenPages.value = pages;

        const loadedResults = await Promise.all(deck.value.map((c, i) => new Promise(res => {
            const img = new Image();
            img.crossOrigin = "Anonymous";
            img.onload = () => res({ img, i, status: "ok" });
            img.onerror = () => res({ img: null, i, status: "error" });
            img.src = c.image_url;
        })));

        const validImg = loadedResults.find(r => r.img);
        let cardW = 734;
        let cardH = 1024;
        if (validImg && validImg.img.naturalWidth > 100) {
            cardW = validImg.img.naturalWidth;
            cardH = validImg.img.naturalHeight;
        }

        for (let p = 0; p < pages; p++) {
            currentGenPage.value = p + 1;
            const canvas = document.createElement("canvas");
            const ctx = canvas.getContext("2d");
            const pageCardsCount = Math.min(cardsPerPage, totalCards - p * cardsPerPage);
            canvas.width = cardW * cols;
            canvas.height = cardH * 3;
            ctx.fillStyle = "#ffffff";
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            for (let i = 0; i < pageCardsCount; i++) {
                const globalIdx = p * cardsPerPage + i;
                const item = loadedResults[globalIdx];
                if (item && item.img) {
                    const col = i % cols;
                    const row = Math.floor(i / cols);
                    ctx.drawImage(item.img, col * cardW, row * cardH, cardW, cardH);
                }
            }
            const link = document.createElement("a");
            const pageSuffix = pages > 1 ? `_page_${p + 1}` : "";
            link.download = `deck_${format}${pageSuffix}.png`;
            link.href = canvas.toDataURL("image/png");
            link.click();
            await new Promise(r => setTimeout(r, 800));
        }
        generating.value = false;
        showPrintModal.value = false;
    };

    const openJpDeckLibrary = async () => {
        showJpDeckLibrary.value = true;
        if (jpDecks.value.length === 0) {
            jpDeckPage.value = 1;
            jpDeckSearchQuery.value = "";
            await searchJpDecks();
        }
    };

    const searchJpDecks = async (page = 1) => {
        jpDecksLoading.value = true;
        try {
            const q = encodeURIComponent(jpDeckSearchQuery.value.trim());
            const sort = jpDeckSortMode.value;
            const res = await fetch(`/api/decks/japanese/list?q=${q}&page=${page}&sort=${sort}`);
            const data = await res.json();
            if (data.success) {
                jpDecks.value = data.decks || [];
                jpDeckPage.value = data.page || 1;
                jpDeckTotalPages.value = data.pages || 1;
                jpSuggestion.value = data.suggestion || null;
            }
        } catch (e) {
            console.error(e);
        } finally {
            jpDecksLoading.value = false;
        }
    };

    const loadJpDeck = async (code) => {
        jpDecksLoading.value = true;
        try {
            const res = await fetch(`/api/decks/japanese/${code}`);
            const data = await res.json();
            if (data.success) {
                const title = data.name || `日本牌組 ${code}`;
                openDeckInTab({
                    source: "japanese",
                    title,
                    cards: data.deck || [],
                    meta: { code }
                }, "匯入日本牌組");
                jpImportMissing.value = data.missing_cards || [];
                if (jpImportMissing.value.length) {
                    openImportMissingNotice({
                        source: "japanese",
                        title,
                        importedCount: data.imported_count || (data.deck || []).length,
                        missing: jpImportMissing.value
                    });
                }
                showJpDeckLibrary.value = false;
            } else {
                alert("載入失敗: " + data.error);
            }
        } catch (e) {
            console.error(e);
            alert("連線失敗");
        } finally {
            jpDecksLoading.value = false;
        }
    };

    const openLiveModal = async () => {
        if (deck.value.length === 0) return alert("牌組是空的");
        showLiveModal.value = true;
        liveDeckString.value = "";
        liveNotFound.value = [];
        isConverting.value = true;
        try {
            const res = await fetch("/api/tools/convert-live", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ deck: deck.value })
            });
            const data = await res.json();
            if (data.success) {
                liveDeckString.value = data.deck_string;
                liveNotFound.value = data.not_found || [];
            } else {
                alert("轉換失敗: " + data.error);
                showLiveModal.value = false;
            }
        } catch (e) {
            alert("連線失敗");
            showLiveModal.value = false;
        } finally {
            isConverting.value = false;
        }
    };

    const copyLiveString = () => navigator.clipboard.writeText(liveDeckString.value).then(() => alert("已複製"));
    const prevJpPage = () => { if (jpDeckPage.value > 1) searchJpDecks(jpDeckPage.value - 1); };
    const nextJpPage = () => { if (jpDeckPage.value < jpDeckTotalPages.value) searchJpDecks(jpDeckPage.value + 1); };

    return {
        showImportModal, importText, isImporting, importStatus, conflictQueue, notFoundList, importIntoNewTab,
        showExportModal, exportTextContent, exportWithId,
        showPrintModal, generating, currentGenPage, totalGenPages,
        showSaveModal, saveDeckName, saveIsPublic, isSaving, showShareModal, savedDeckId, shareUrl,
        showDeckLibrary, publicDecks, publicDecksLoading, publicDeckSearchQuery,
        openImportModal, startImport, resolveConflict, skipConflict,
        openExportModal, regenerateExportText, copyExportText,
        openPrintModal, generateDeckImage,
        openSaveModal, submitSaveDeck, copyShareLink,
        openDeckLibrary, searchPublicDecks, loadPublicDeck,
        showJpDeckLibrary, jpDecks, jpDecksLoading, jpDeckSearchQuery, jpDeckSortMode, jpDeckPage, jpDeckTotalPages, jpSuggestion, jpImportMissing,
        openJpDeckLibrary, searchJpDecks, loadJpDeck, prevJpPage, nextJpPage, searchTag,
        showLimitlessDeckLibrary, limitlessTournaments, limitlessTournamentsLoading,
        selectedLimitlessTournament, limitlessDecks, limitlessDecksLoading, limitlessDeckSearchQuery,
        limitlessDeckSortMode, limitlessDeckRegion, limitlessDeckFormat, limitlessDeckPage,
        limitlessDeckTotalPages, limitlessTournamentPage, limitlessTournamentTotalPages,
        selectedLimitlessDeck, limitlessDetailLoading, limitlessCardsLoading, limitlessLang,
        limitlessMode, openLimitlessDeckLibrary, searchLimitlessDecks, openLimitlessDeck,
        searchLimitlessTournaments, openLimitlessTournament, importLimitlessDeck,
        limitlessImporting, limitlessImportMissing, importMissingNotice, closeImportMissingNotice,
        loadLimitlessCards, setLimitlessLang, setLimitlessMode,
        closeLimitlessDeckDetail, getLimitlessCards, getLimitlessSectionCards,
        getLimitlessSectionCount, getLimitlessDeckName, getLimitlessTagName,
        getLimitlessSectionName, prevLimitlessPage, nextLimitlessPage,
        showJpPreviewModal, currentJpPreviewImage, openJpPreview,
        showLiveModal, liveDeckString, liveNotFound, isConverting,
        openLiveModal, copyLiveString
    };
}
