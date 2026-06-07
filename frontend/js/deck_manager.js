// /Pokemon/public/js/deck_manager.js

function useDeckManager() {
    const { ref, computed } = Vue;

    const makeId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    const cloneCard = (card) => ({
        ...card,
        uniqueId: makeId()
    });
    const cloneDeckCards = (cards) => (Array.isArray(cards) ? cards : []).map(card => cloneCard(card));
    const snapshotDeck = (cards) => JSON.parse(JSON.stringify(cards || []));

    const createDeckTab = ({
        title = '新牌組',
        source = 'scratch',
        cards = [],
        workspaceItem = null,
        workspaceItemId = null,
        meta = {},
        dirty = false
    } = {}) => {
        const deck = cloneDeckCards(cards);
        return {
            id: makeId(),
            title,
            source,
            deck,
            workspaceItem,
            workspaceItemId: workspaceItemId || (workspaceItem ? workspaceItem.id : null),
            dirty,
            meta,
            selectedCards: new Set(),
            history: [{ deck: snapshotDeck(deck), action: '載入牌組', time: Date.now() }],
            historyIndex: 0
        };
    };

    const deckTabs = ref([createDeckTab()]);
    const activeDeckTabId = ref(deckTabs.value[0].id);
    const isStandardMode = ref(true);
    const isDragOver = ref(false);
    const isLeftDragOver = ref(false);
    let longPressTimer = null;
    let isLongPressEvent = false;

    const pendingTimelineAction = ref(null);
    let suppressTimelineActions = false;
    let batchDepth = 0;
    let batchTouched = false;

    const activeDeckTab = computed(() => {
        let tab = deckTabs.value.find(item => item && item.id === activeDeckTabId.value);
        if (!tab) {
            tab = deckTabs.value.find(Boolean);
            if (tab) activeDeckTabId.value = tab.id;
        }
        return tab || null;
    });
    const deckTabsForDisplay = computed(() => deckTabs.value.filter(tab => tab && tab.id));

    const deck = computed({
        get() {
            return activeDeckTab.value ? activeDeckTab.value.deck : [];
        },
        set(value) {
            const tab = activeDeckTab.value;
            if (!tab) return;
            tab.deck = Array.isArray(value) ? value : [];
            markActiveTabDirty();
        }
    });

    const currentDeckName = computed({
        get() {
            return activeDeckTab.value ? activeDeckTab.value.title : '';
        },
        set(value) {
            const tab = activeDeckTab.value;
            if (!tab) return;
            tab.title = value || '未命名牌組';
        }
    });

    const selectedCards = computed({
        get() {
            return activeDeckTab.value ? activeDeckTab.value.selectedCards : new Set();
        },
        set(value) {
            const tab = activeDeckTab.value;
            if (!tab) return;
            tab.selectedCards = value instanceof Set ? value : new Set(value || []);
        }
    });

    const history = computed({
        get() {
            return activeDeckTab.value ? activeDeckTab.value.history : [];
        },
        set(value) {
            if (activeDeckTab.value) activeDeckTab.value.history = value || [];
        }
    });

    const historyIndex = computed({
        get() {
            return activeDeckTab.value ? activeDeckTab.value.historyIndex : 0;
        },
        set(value) {
            if (activeDeckTab.value) activeDeckTab.value.historyIndex = value;
        }
    });

    const activeDeckTabDirty = computed(() => !!(activeDeckTab.value && activeDeckTab.value.dirty));
    const activeWorkspaceItem = computed(() => {
        const tab = activeDeckTab.value;
        return tab && tab.source === 'workspace' ? tab.workspaceItem : null;
    });

    const markTimelineAction = (actionName) => {
        if (!suppressTimelineActions && actionName) {
            pendingTimelineAction.value = actionName;
        }
    };

    const markActiveTabDirty = (actionName = null) => {
        const tab = activeDeckTab.value;
        if (!tab) return;
        tab.dirty = true;
        if (actionName) markTimelineAction(actionName);
    };

    const clearActiveTabDirty = () => {
        const tab = activeDeckTab.value;
        if (tab) tab.dirty = false;
    };

    const consumeTimelineAction = () => {
        const action = pendingTimelineAction.value || '修改牌組';
        pendingTimelineAction.value = null;
        return action;
    };

    const recordHistory = (actionName = '修改牌組', { dirty = true } = {}) => {
        const tab = activeDeckTab.value;
        if (!tab) return;

        if (tab.historyIndex < tab.history.length - 1) {
            tab.history = tab.history.slice(0, tab.historyIndex + 1);
        }

        tab.history.push({
            deck: snapshotDeck(tab.deck),
            action: actionName,
            time: Date.now()
        });
        tab.historyIndex++;

        if (tab.history.length > 50) {
            tab.history.shift();
            tab.historyIndex--;
        }

        if (dirty) {
            tab.dirty = true;
            markTimelineAction(actionName);
        }
    };

    const noteDeckMutation = (actionName) => {
        const tab = activeDeckTab.value;
        if (!tab) return;
        tab.dirty = true;
        if (batchDepth > 0) {
            batchTouched = true;
            markTimelineAction(actionName);
            return;
        }
        recordHistory(actionName);
    };

    const withTimelineBatch = (actionName, callback) => {
        batchDepth++;
        suppressTimelineActions = true;
        try {
            const result = callback();
            return result;
        } finally {
            batchDepth--;
            suppressTimelineActions = false;
            if (batchDepth === 0 && batchTouched) {
                batchTouched = false;
                recordHistory(actionName);
            }
            markTimelineAction(actionName);
        }
    };

    const resetHistory = () => {
        const tab = activeDeckTab.value;
        if (!tab) return;
        tab.history = [{ deck: snapshotDeck(tab.deck), action: '載入牌組', time: Date.now() }];
        tab.historyIndex = 0;
    };

    const undo = () => {
        const tab = activeDeckTab.value;
        if (!tab || tab.historyIndex <= 0) return;
        tab.historyIndex--;
        tab.deck = snapshotDeck(tab.history[tab.historyIndex].deck);
        tab.selectedCards.clear();
        tab.dirty = true;
    };

    const redo = () => {
        const tab = activeDeckTab.value;
        if (!tab || tab.historyIndex >= tab.history.length - 1) return;
        tab.historyIndex++;
        tab.deck = snapshotDeck(tab.history[tab.historyIndex].deck);
        tab.selectedCards.clear();
        tab.dirty = true;
    };

    const activateDeckTab = (tabId) => {
        if (deckTabs.value.some(tab => tab && tab.id === tabId)) {
            activeDeckTabId.value = tabId;
            return true;
        }
        return false;
    };

    const openWorkspaceTab = (item, cards = null) => {
        if (!item || item.type !== 'deck') return null;
        const existing = deckTabs.value.find(tab => tab && tab.source === 'workspace' && tab.workspaceItemId === item.id);
        if (existing) {
            existing.workspaceItem = item;
            existing.title = item.name || existing.title;
            activeDeckTabId.value = existing.id;
            return existing;
        }

        const tab = createDeckTab({
            title: item.name || 'Workspace 牌組',
            source: 'workspace',
            cards: cards || item.content || [],
            workspaceItem: item,
            workspaceItemId: item.id,
            dirty: false
        });
        deckTabs.value.push(tab);
        activeDeckTabId.value = tab.id;
        return tab;
    };

    const openImportedDeckTab = ({ source = 'scratch', title = '未命名牌組', cards = [], meta = {} } = {}) => {
        const tab = createDeckTab({
            title,
            source,
            cards,
            meta,
            dirty: source !== 'workspace'
        });
        deckTabs.value.push(tab);
        activeDeckTabId.value = tab.id;
        return tab;
    };

    const bindActiveTabToWorkspace = (item) => {
        const tab = activeDeckTab.value;
        if (!tab || !item) return null;
        tab.source = 'workspace';
        tab.workspaceItem = item;
        tab.workspaceItemId = item.id;
        tab.title = item.name || tab.title;
        tab.dirty = false;
        resetHistory();
        return tab;
    };

    const closeDeckTab = (tabId, options = {}) => {
        const tab = deckTabs.value.find(item => item && item.id === tabId);
        if (!tab) return false;
        if (tab.dirty && !options.force) {
            const ok = confirm(`「${tab.title}」尚未儲存，確定要關閉分頁嗎？`);
            if (!ok) return false;
        }

        const idx = deckTabs.value.findIndex(item => item && item.id === tabId);
        if (idx < 0) return false;
        deckTabs.value.splice(idx, 1);

        if (deckTabs.value.length === 0) {
            const fresh = createDeckTab();
            deckTabs.value.push(fresh);
            activeDeckTabId.value = fresh.id;
        } else if (activeDeckTabId.value === tabId) {
            const next = deckTabs.value[Math.min(idx, deckTabs.value.length - 1)] || deckTabs.value.find(Boolean);
            if (next) activeDeckTabId.value = next.id;
        }
        return true;
    };

    const getDeckTabSourceLabel = (tab) => ({
        workspace: 'Workspace',
        scratch: 'Draft',
        ai: 'AI',
        limitless: 'Limitless',
        japanese: 'JP',
        public: 'Public'
    }[tab && tab.source] || 'Deck');

    const getDeckTabSourceIcon = (tab) => ({
        workspace: 'fa-folder-open',
        scratch: 'fa-file-lines',
        ai: 'fa-robot',
        limitless: 'fa-trophy',
        japanese: 'fa-torii-gate',
        public: 'fa-globe'
    }[tab && tab.source] || 'fa-layer-group');

    const totalCards = computed(() => deck.value.length);

    const getCountColorClass = computed(() =>
        isStandardMode.value
            ? (totalCards.value === 60 ? 'text-green-400' : totalCards.value > 60 ? 'text-red-400' : 'text-white')
            : 'text-purple-400'
    );

    const groupedDeck = computed(() => {
        const groups = { 'Pokemon': [], 'Trainer': [], 'Energy': [] };
        deck.value.forEach(card => {
            let typeKey = 'Trainer';
            const cType = card.card_type ? card.card_type.toLowerCase() : '';
            if (cType.includes('pokemon') || cType.includes('pokémon')) typeKey = 'Pokemon';
            else if (cType.includes('energy')) typeKey = 'Energy';
            groups[typeKey].push(card);
        });
        for (const key in groups) groups[key].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
        if (groups.Pokemon.length === 0) delete groups.Pokemon;
        if (groups.Trainer.length === 0) delete groups.Trainer;
        if (groups.Energy.length === 0) delete groups.Energy;
        return groups;
    });

    const getCardDisplayName = (card) => String(card && (card.name || card.card_name || card.card_id || card.id) || '');
    const getSameNameCount = (card) => {
        const name = getCardDisplayName(card);
        return deck.value.filter(item => getCardDisplayName(item) === name).length;
    };
    const getDeckDisplayGroup = (card) => {
        const cardType = String(card && card.card_type || '').toLowerCase();
        const subType = String(card && card.sub_type || '').toLowerCase();
        const text = `${cardType} ${subType}`;

        if (text.includes('energy')) return 'Energy';
        if (text.includes('pokemon') || text.includes('pokémon') || text.includes('pok矇mon')) return 'Pokemon';
        if (text.includes('supporter') || text.includes('支援者')) return '支援者';
        if (text.includes('item') || text.includes('物品')) return '物品';
        if (text.includes('tool') || text.includes('道具')) return '道具';
        if (text.includes('stadium') || text.includes('競技場')) return '競技場';
        return '其他訓練家';
    };
    const sortCardsForDisplay = (a, b) => {
        const countDiff = getSameNameCount(b) - getSameNameCount(a);
        if (countDiff !== 0) return countDiff;
        const nameDiff = getCardDisplayName(a).localeCompare(getCardDisplayName(b), 'zh-Hant');
        if (nameDiff !== 0) return nameDiff;
        return String(a.card_id || a.id || '').localeCompare(String(b.card_id || b.id || ''));
    };
    const groupedDeckDisplay = computed(() => {
        const order = ['Pokemon', '支援者', '物品', '道具', '競技場', '其他訓練家', 'Energy'];
        const buckets = Object.fromEntries(order.map(key => [key, []]));
        deck.value.forEach(card => {
            buckets[getDeckDisplayGroup(card)].push(card);
        });

        const groups = {};
        order.forEach(key => {
            if (buckets[key].length) groups[key] = buckets[key].slice().sort(sortCardsForDisplay);
        });
        return groups;
    });

    const stats = computed(() => {
        const s = { pokemon: 0, trainers: 0, energy: 0 };
        deck.value.forEach(c => {
            const type = c.card_type ? c.card_type.toLowerCase() : '';
            if (type.includes('pokemon') || type.includes('pokémon')) s.pokemon++;
            else if (type.includes('energy')) s.energy++;
            else s.trainers++;
        });
        return s;
    });

    const getGroupCount = (arr) => arr.length;
    const getCardCountInDeck = (name) => deck.value.filter(c => c.name === name).length;
    const getCardCountById = (cardId) => deck.value.filter(c => c.card_id === cardId).length;

    const addToDeck = (card) => {
        if (!card) return false;
        if (isStandardMode.value) {
            if (deck.value.length >= 60) {
                alert('標準牌組最多 60 張。');
                return false;
            }
            const currentCount = getCardCountInDeck(card.name);
            const type = card.card_type || '';
            if (currentCount >= 4 && !type.includes('Energy')) {
                alert(`${card.name} 已達 4 張上限。`);
                return false;
            }
        }
        deck.value.push(cloneCard(card));
        noteDeckMutation(`加入 ${card.name || '卡片'}`);
        return true;
    };

    const removeCardInstance = (uniqueId) => {
        const idx = deck.value.findIndex(c => c.uniqueId === uniqueId);
        if (idx !== -1) {
            const removedName = deck.value[idx].name || '卡片';
            deck.value.splice(idx, 1);
            if (selectedCards.value.has(uniqueId)) selectedCards.value.delete(uniqueId);
            noteDeckMutation(`移除 ${removedName}`);
        }
    };

    const clearDeck = () => {
        if (confirm('確定要清空目前分頁的牌組嗎？')) {
            deck.value = [];
            selectedCards.value.clear();
            noteDeckMutation('清空牌組');
        }
    };

    const toggleSelection = (uniqueId) => {
        if (selectedCards.value.has(uniqueId)) selectedCards.value.delete(uniqueId);
        else selectedCards.value.add(uniqueId);
    };

    const handleCardClick = (card, openModalCallback) => {
        if (isLongPressEvent) {
            isLongPressEvent = false;
            return;
        }

        if (selectedCards.value.size > 0) {
            toggleSelection(card.uniqueId);
        } else if (openModalCallback) {
            openModalCallback(card);
        }
    };

    const handleMouseDown = (uniqueId) => {
        isLongPressEvent = false;
        longPressTimer = setTimeout(() => {
            if (!selectedCards.value.has(uniqueId)) toggleSelection(uniqueId);
            isLongPressEvent = true;
            if (navigator.vibrate) navigator.vibrate(50);
        }, 500);
    };

    const handleMouseUp = () => {
        if (longPressTimer) {
            clearTimeout(longPressTimer);
            longPressTimer = null;
        }
    };

    const clearSelection = () => {
        selectedCards.value.clear();
    };

    const deckContextMenu = Vue.reactive({
        visible: false,
        x: 0,
        y: 0,
        card: null,
        index: -1
    });

    const openDeckContextMenu = (event, card, index) => {
        if (event) event.stopPropagation();
        deckContextMenu.visible = true;
        deckContextMenu.x = event.clientX;
        deckContextMenu.y = event.clientY;
        deckContextMenu.card = card;
        deckContextMenu.index = index;
    };

    const closeDeckContextMenu = () => {
        deckContextMenu.visible = false;
        deckContextMenu.card = null;
        deckContextMenu.index = -1;
    };

    const handleDeckContextAction = (action) => {
        if (!deckContextMenu.card || deckContextMenu.index === -1) return;
        const { card, index } = deckContextMenu;

        switch (action) {
            case 'delete':
                if (selectedCards.value.has(card.uniqueId)) {
                    if (confirm(`刪除選取的 ${selectedCards.value.size} 張卡片？`)) {
                        deck.value = deck.value.filter(c => !selectedCards.value.has(c.uniqueId));
                        selectedCards.value.clear();
                        noteDeckMutation('移除選取卡片');
                    }
                } else {
                    deck.value.splice(index, 1);
                    noteDeckMutation(`移除 ${card.name || '卡片'}`);
                }
                break;
            case 'addOne':
                deck.value.splice(index + 1, 0, cloneCard(card));
                noteDeckMutation(`加入 ${card.name || '卡片'}`);
                break;
            case 'addMultiple': {
                const countStr = prompt(`要新增幾張 ${card.name || '卡片'}？`, '3');
                const count = parseInt(countStr);
                if (!isNaN(count) && count > 0) {
                    const newCards = Array(count).fill().map(() => cloneCard(card));
                    deck.value.splice(index + 1, 0, ...newCards);
                    noteDeckMutation(`加入 ${count} 張 ${card.name || '卡片'}`);
                }
                break;
            }
        }
        closeDeckContextMenu();
    };

    const handleDragStart = (e, card, source) => {
        e.dataTransfer.setData('application/json', JSON.stringify({ card, source }));
        e.dataTransfer.effectAllowed = source === 'deck' ? 'move' : 'copy';
    };

    const handleDragOver = () => { isDragOver.value = true; };
    const handleDragLeave = () => { isDragOver.value = false; };

    const handleDrop = (e) => {
        isDragOver.value = false;
        const raw = e.dataTransfer.getData('application/json');
        if (!raw) return;
        const { card, source } = JSON.parse(raw);
        if (source === 'search') addToDeck(card);
    };

    const handleLeftDragOver = (e) => {
        e.preventDefault();
        isLeftDragOver.value = true;
    };
    const handleLeftDragLeave = () => { isLeftDragOver.value = false; };

    const handleLeftDrop = (e) => {
        isLeftDragOver.value = false;
        const raw = e.dataTransfer.getData('application/json');
        if (!raw) return;
        const { card, source } = JSON.parse(raw);
        if (source === 'deck') removeCardInstance(card.uniqueId);
    };

    return {
        deckTabs,
        deckTabsForDisplay,
        activeDeckTabId,
        activeDeckTab,
        activeDeckTabDirty,
        activeWorkspaceItem,
        deck,
        isStandardMode,
        currentDeckName,
        selectedCards,
        isDragOver,
        isLeftDragOver,
        history,
        historyIndex,
        totalCards,
        getCountColorClass,
        groupedDeck: groupedDeckDisplay,
        stats,
        getGroupCount,
        getCardCountInDeck,
        getCardCountById,
        addToDeck,
        removeCardInstance,
        clearDeck,
        toggleSelection,
        handleCardClick,
        handleMouseDown,
        handleMouseUp,
        clearSelection,
        handleDragStart,
        handleDrop,
        handleDragOver,
        handleDragLeave,
        handleLeftDrop,
        handleLeftDragOver,
        handleLeftDragLeave,
        deckContextMenu,
        openDeckContextMenu,
        closeDeckContextMenu,
        handleDeckContextAction,
        undo,
        redo,
        resetHistory,
        markTimelineAction,
        consumeTimelineAction,
        withTimelineBatch,
        recordHistory,
        markActiveTabDirty,
        clearActiveTabDirty,
        activateDeckTab,
        closeDeckTab,
        openWorkspaceTab,
        openImportedDeckTab,
        bindActiveTabToWorkspace,
        getDeckTabSourceLabel,
        getDeckTabSourceIcon
    };
}
