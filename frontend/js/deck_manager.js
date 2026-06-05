// /Pokemon/public/js/deck_manager.js

function useDeckManager() {
    const { ref, computed, reactive } = Vue;

    // === 狀態 ===
    const deck = ref([]);
    const isStandardMode = ref(true);
    const currentDeckName = ref('');
    const selectedCards = ref(new Set()); // 儲存被選取卡片的 uniqueId
    const isDragOver = ref(false);
    const isLeftDragOver = ref(false);
    let longPressTimer = null;
    let isLongPressEvent = false;

    // === History / Timeline ===
    const history = ref([{ deck: [], action: '初始狀態', time: Date.now() }]);
    const historyIndex = ref(0);
    const pendingTimelineAction = ref(null);
    let suppressTimelineActions = false;

    const markTimelineAction = (actionName) => {
        if (!suppressTimelineActions && actionName) {
            pendingTimelineAction.value = actionName;
        }
    };

    const consumeTimelineAction = () => {
        const action = pendingTimelineAction.value || '編輯牌組';
        pendingTimelineAction.value = null;
        return action;
    };

    const withTimelineBatch = (actionName, callback) => {
        suppressTimelineActions = true;
        try {
            const result = callback();
            recordHistory(actionName);
            return result;
        } finally {
            suppressTimelineActions = false;
            markTimelineAction(actionName);
        }
    };

    const recordHistory = (actionName) => {
        // 如果目前不在歷史的最前端，切斷後續（新的分支）
        if (historyIndex.value < history.value.length - 1) {
            history.value = history.value.slice(0, historyIndex.value + 1);
        }
        
        const snapshot = JSON.parse(JSON.stringify(deck.value));
        history.value.push({
            deck: snapshot,
            action: actionName,
            time: Date.now()
        });
        markTimelineAction(actionName);
        historyIndex.value++;
        
        // 限制長度
        if (history.value.length > 50) {
            history.value.shift();
            historyIndex.value--;
        }
    };

    const undo = () => {
        if (historyIndex.value > 0) {
            historyIndex.value--;
            const prevState = history.value[historyIndex.value];
            deck.value = JSON.parse(JSON.stringify(prevState.deck));
            // 恢復選取狀態? 這裡先清空選取以免 ID 不存在
            selectedCards.value.clear();
        }
    };

    const redo = () => {
        if (historyIndex.value < history.value.length - 1) {
            historyIndex.value++;
            const nextState = history.value[historyIndex.value];
            deck.value = JSON.parse(JSON.stringify(nextState.deck));
            selectedCards.value.clear();
        }
    };

    // === 計算屬性 ===
    const totalCards = computed(() => deck.value.length);
    
    const getCountColorClass = computed(() => 
        isStandardMode.value ? (totalCards.value === 60 ? 'text-green-400' : totalCards.value > 60 ? 'text-red-400' : 'text-white') : 'text-purple-400'
    );

    const groupedDeck = computed(() => {
        const groups = { 'Pokémon': [], 'Trainer': [], 'Energy': [] };
        deck.value.forEach(card => {
            let typeKey = 'Trainer';
            const cType = card.card_type ? card.card_type.toLowerCase() : '';
            if (cType.includes('pokémon') || cType.includes('pokemon')) typeKey = 'Pokémon';
            else if (cType.includes('energy')) typeKey = 'Energy';
            groups[typeKey].push(card);
        });
        for (const key in groups) groups[key].sort((a, b) => a.name.localeCompare(b.name));
        if (groups['Pokémon'].length === 0) delete groups['Pokémon'];
        if (groups['Trainer'].length === 0) delete groups['Trainer'];
        if (groups['Energy'].length === 0) delete groups['Energy'];
        return groups;
    });

    const stats = computed(() => {
        const s = { pokemon: 0, trainers: 0, energy: 0 };
        deck.value.forEach(c => {
            const type = c.card_type ? c.card_type.toLowerCase() : '';
            if (type.includes('pokémon')) s.pokemon++;
            else if (type.includes('energy')) s.energy++;
            else s.trainers++;
        });
        return s;
    });

    const getGroupCount = (arr) => arr.length;
    const getCardCountInDeck = (name) => deck.value.filter(c => c.name === name).length;
    const getCardCountById = (cardId) => deck.value.filter(c => c.card_id === cardId).length;

    // === 動作 ===
    const addToDeck = (card) => {
        if (isStandardMode.value) {
            if (deck.value.length >= 60) {
                alert("標準模式上限 60 張！");
                return false;
            }
            const currentCount = getCardCountInDeck(card.name);
            if (currentCount >= 4 && !card.card_type.includes("Energy")) {
                alert(`無法加入：${card.name} 已達 4 張上限`);
                return false;
            }
        }
        deck.value.push({ ...card, uniqueId: Date.now() + Math.random().toString(36).substr(2, 9) });
        recordHistory(`新增 ${card.name}`);
        return true;
    };

    const removeCardInstance = (uniqueId) => {
        const idx = deck.value.findIndex(c => c.uniqueId === uniqueId);
        if (idx !== -1) {
            const removedName = deck.value[idx].name;
            deck.value.splice(idx, 1);
            if (selectedCards.value.has(uniqueId)) selectedCards.value.delete(uniqueId);
            recordHistory(`移除 ${removedName}`);
        }
    };

    const clearDeck = () => {
        if (confirm("清空牌組？")) {
            deck.value = [];
            selectedCards.value.clear();
            currentDeckName.value = '';
            recordHistory('清空牌組');
        }
    };

    // === 選取與互動 ===
    const toggleSelection = (uniqueId) => {
        if (selectedCards.value.has(uniqueId)) selectedCards.value.delete(uniqueId);
        else selectedCards.value.add(uniqueId);
    };

    // [修改重點] 單點擊處理邏輯
    const handleCardClick = (card, openModalCallback) => {
        // 1. 如果是長按剛結束，不執行任何動作 (因為長按已經觸發了選取)
        if (isLongPressEvent) { 
            isLongPressEvent = false; 
            return; 
        }

        // 2. 檢查是否處於「多選模式」 (即已有卡片被選取)
        if (selectedCards.value.size > 0) {
            // 多選模式下：單點 = 切換選取狀態
            toggleSelection(card.uniqueId);
        } else {
            // 普通模式下：單點 = 打開詳情
            if (openModalCallback) {
                openModalCallback(card);
            }
        }
    };

    const handleMouseDown = (uniqueId) => {
        isLongPressEvent = false;
        longPressTimer = setTimeout(() => {
            // 長按觸發：選取該卡片 (進入多選模式)
            if (!selectedCards.value.has(uniqueId)) {
                toggleSelection(uniqueId);
            }
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

    // 點擊空白處取消所有選取 (可選功能，建議在 app.js 的 handleGlobalClick 呼叫)
    const clearSelection = () => {
        selectedCards.value.clear();
    };

    // === 右鍵選單 ===
    const deckContextMenu = reactive({
        visible: false,
        x: 0,
        y: 0,
        card: null,
        index: -1
    });

    const openDeckContextMenu = (event, card, index) => {
        if(event) event.stopPropagation(); // [新增]
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

    const cloneCard = (card) => {
        return {
            ...card,
            uniqueId: Date.now().toString(36) + Math.random().toString(36).substr(2, 5)
        };
    };

    const handleDeckContextAction = (action) => {
        if (!deckContextMenu.card || deckContextMenu.index === -1) return;
        const { card, index } = deckContextMenu;

        switch (action) {
            case 'delete':
                // [修改重點] 刪除邏輯
                if (selectedCards.value.has(card.uniqueId)) {
                    // 如果右鍵點擊的是「已選取」的卡片 -> 刪除所有已選取的卡片
                    if (confirm(`確定刪除選取的 ${selectedCards.value.size} 張卡片嗎？`)) {
                        deck.value = deck.value.filter(c => !selectedCards.value.has(c.uniqueId));
                        selectedCards.value.clear(); // 刪除後退出多選模式
                    }
                } else {
                    // 如果右鍵點擊的是「未選取」的卡片 -> 只刪除這一張
                    deck.value.splice(index, 1);
                }
                break;
            case 'addOne':
                deck.value.splice(index + 1, 0, cloneCard(card));
                break;
            case 'addMultiple':
                const countStr = prompt(`要新增幾張「${card.name}」？`, "3");
                const count = parseInt(countStr);
                if (!isNaN(count) && count > 0) {
                    const newCards = Array(count).fill().map(() => cloneCard(card));
                    deck.value.splice(index + 1, 0, ...newCards);
                }
                break;
        }
        closeDeckContextMenu();
    };

    // === 拖曳邏輯 ===
    const handleDragStart = (e, card, source) => {
        e.dataTransfer.setData('application/json', JSON.stringify({ card: card, source: source }));
        e.dataTransfer.effectAllowed = source === 'deck' ? 'move' : 'copy';
    };

    const handleDragOver = (e) => { isDragOver.value = true; };
    const handleDragLeave = (e) => { isDragOver.value = false; };
    
    const handleDrop = (e) => {
        isDragOver.value = false;
        const raw = e.dataTransfer.getData('application/json');
        if (!raw) return;
        const { card, source } = JSON.parse(raw);
        if (source === 'search') addToDeck(card);
    };

    const handleLeftDragOver = (e) => { e.preventDefault(); isLeftDragOver.value = true; };
    const handleLeftDragLeave = (e) => { isLeftDragOver.value = false; };
    
    const handleLeftDrop = (e) => {
        isLeftDragOver.value = false;
        const raw = e.dataTransfer.getData('application/json');
        if (!raw) return;
        const { card, source } = JSON.parse(raw);
        if (source === 'deck') removeCardInstance(card.uniqueId);
    };

    const resetHistory = () => {
        const snapshot = JSON.parse(JSON.stringify(deck.value));
        history.value = [{ deck: snapshot, action: '重置/載入', time: Date.now() }];
        historyIndex.value = 0;
    };

    return {
        deck, isStandardMode, currentDeckName, selectedCards, isDragOver, isLeftDragOver,
        history, historyIndex, // [新增]
        totalCards, getCountColorClass, groupedDeck, stats, getGroupCount, getCardCountInDeck, getCardCountById,
        addToDeck, removeCardInstance, clearDeck,
        toggleSelection, handleCardClick, handleMouseDown, handleMouseUp, clearSelection,
        handleDragStart, handleDrop, handleDragOver, handleDragLeave,
        handleLeftDrop, handleLeftDragOver, handleLeftDragLeave,
        deckContextMenu, openDeckContextMenu, closeDeckContextMenu, handleDeckContextAction,
        undo, redo, resetHistory, markTimelineAction, consumeTimelineAction, withTimelineBatch // [新增]
    };
}
