// /Pokemon/public/js/workspace.js

function useWorkspace(deck, currentDeckName, isAuthenticated, timelineActions = null) {
    const { ref, reactive, computed, watch } = Vue;

    // === 狀態 ===
    const workspaceVisible = ref(true);  // 側邊欄是否顯示
    const workspaceTree = ref([]);       // 工作區樹狀結構
    const workspaceLoading = ref(false);
    const currentItem = ref(null);       // 當前打開的項目
    const hasUnsavedChanges = ref(false);
    const autoSaveTimer = ref(null);
    const timelineEntries = ref([]);
    const timelineLoading = ref(false);
    const timelineRestoring = ref(false);
    let suppressAutoSave = false;
    
    // 右鍵選單
    const wsContextMenu = reactive({
        visible: false,
        x: 0,
        y: 0,
        item: null
    });
    
    // 新增/編輯 Modal
    const wsModal = reactive({
        visible: false,
        mode: 'create',  // 'create', 'rename', 'createFolder'
        parentId: null,
        itemId: null,
        name: '',
        type: 'deck'
    });
    
    // 展開的資料夾 ID Set
    const expandedFolders = ref(new Set());

    // === 計算屬性 ===
    const isWorkspaceAvailable = computed(() => isAuthenticated.value);

    // === 監聽牌組變化，標記未儲存 ===
    watch(deck, (newVal) => {
        if (!suppressAutoSave && currentItem.value && currentItem.value.type === 'deck') {
            if (newVal === currentItem.value.content) return;
            hasUnsavedChanges.value = true;
            // 自動儲存 (防抖 2 秒)
            if (autoSaveTimer.value) {
                clearTimeout(autoSaveTimer.value);
            }
            autoSaveTimer.value = setTimeout(() => {
                autoSaveDeck();
            }, 2000);
        }
    }, { deep: true });

    // === API 操作 ===
    
    // 載入工作區
    const loadWorkspace = async () => {
        if (!isAuthenticated.value) return;
        
        workspaceLoading.value = true;
        try {
            const res = await fetch('/api/workspace');
            const data = await res.json();
            if (data.success) {
                workspaceTree.value = data.workspace;
            }
        } catch (e) {
            console.error("Load workspace error:", e);
        } finally {
            workspaceLoading.value = false;
        }
    };

    // 自動儲存當前牌組
    const autoSaveDeck = async () => {
        if (!currentItem.value || currentItem.value.type !== 'deck') return;
        if (!hasUnsavedChanges.value) return;
        
        try {
            const res = await fetch(`/api/workspace/item/${currentItem.value.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    content: deck.value,
                    timeline_action: timelineActions && timelineActions.consumeTimelineAction
                        ? timelineActions.consumeTimelineAction()
                        : '編輯牌組',
                    timeline_source: 'editor'
                })
            });
            const data = await res.json();
            if (data.success) {
                hasUnsavedChanges.value = false;
                // 更新本地樹狀結構中的卡片數量
                updateItemInTree(currentItem.value.id, { card_count: deck.value.length });
                if (data.timeline) {
                    await loadTimeline();
                }
            }
        } catch (e) {
            console.error("Auto save error:", e);
        }
    };

    // 手動儲存
    const saveCurrentDeck = async () => {
        if (autoSaveTimer.value) {
            clearTimeout(autoSaveTimer.value);
        }
        await autoSaveDeck();
    };

    const formatTimelineTime = (value) => {
        if (!value) return '';
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return value;
        return d.toLocaleString('zh-TW', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    };

    const loadTimeline = async () => {
        if (!currentItem.value || currentItem.value.type !== 'deck') {
            timelineEntries.value = [];
            return;
        }
        timelineLoading.value = true;
        try {
            const res = await fetch(`/api/workspace/item/${currentItem.value.id}/timeline`);
            const data = await res.json();
            if (data.success) {
                timelineEntries.value = data.timeline || [];
            }
        } catch (e) {
            console.error("Load timeline error:", e);
        } finally {
            timelineLoading.value = false;
        }
    };

    const restoreTimeline = async (entry) => {
        if (!currentItem.value || !entry || timelineRestoring.value) return;
        if (!confirm(`還原到「${entry.action || '這個版本'}」？目前牌組會被覆蓋。`)) return;
        timelineRestoring.value = true;
        try {
            const res = await fetch(`/api/workspace/item/${currentItem.value.id}/timeline/${entry.id}/restore`, {
                method: 'POST'
            });
            const data = await res.json();
            if (data.success) {
                suppressAutoSave = true;
                deck.value.length = 0;
                const content = data.content || [];
                content.forEach(card => {
                    deck.value.push({
                        ...card,
                        uniqueId: Date.now() + Math.random().toString(36).substr(2, 9)
                    });
                });
                suppressAutoSave = false;
                hasUnsavedChanges.value = false;
                updateItemInTree(currentItem.value.id, { card_count: deck.value.length });
                if (timelineActions && timelineActions.resetHistory) {
                    timelineActions.resetHistory();
                }
                await loadTimeline();
            } else {
                alert(data.error || 'Timeline restore failed');
            }
        } catch (e) {
            suppressAutoSave = false;
            console.error("Restore timeline error:", e);
            alert('Timeline restore failed');
        } finally {
            suppressAutoSave = false;
            timelineRestoring.value = false;
        }
    };

    // 建立項目
    const createItem = async () => {
        if (!wsModal.name.trim()) {
            alert('請輸入名稱');
            return;
        }
        
        try {
            const res = await fetch('/api/workspace/item', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: wsModal.name.trim(),
                    type: wsModal.type,
                    parent_id: wsModal.parentId,
                    content: wsModal.type === 'deck' ? deck.value : []
                })
            });
            const data = await res.json();
            if (data.success) {
                await loadWorkspace();
                wsModal.visible = false;
                
                // 如果是新建牌組，自動打開它
                if (wsModal.type === 'deck') {
                    openDeck(data.item);
                }
                
                // 如果在資料夾內建立，自動展開該資料夾
                if (wsModal.parentId) {
                    expandedFolders.value.add(wsModal.parentId);
                }
            } else {
                alert('建立失敗: ' + data.error);
            }
        } catch (e) {
            alert('連線錯誤');
        }
    };

    // 重新命名項目
    const renameItem = async () => {
        if (!wsModal.name.trim() || !wsModal.itemId) return;
        
        try {
            const res = await fetch(`/api/workspace/item/${wsModal.itemId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: wsModal.name.trim() })
            });
            const data = await res.json();
            if (data.success) {
                await loadWorkspace();
                wsModal.visible = false;
                
                // 如果重新命名的是當前打開的項目，更新標題
                if (currentItem.value && currentItem.value.id === wsModal.itemId) {
                    currentItem.value.name = wsModal.name.trim();
                    currentDeckName.value = wsModal.name.trim();
                }
            } else {
                alert('重新命名失敗: ' + data.error);
            }
        } catch (e) {
            alert('連線錯誤');
        }
    };

    // 刪除項目
    const deleteItem = async (item) => {
        const typeText = item.type === 'folder' ? '資料夾（包含所有內容）' : '牌組';
        if (!confirm(`確定要刪除${typeText}「${item.name}」嗎？\n此操作無法撤銷！`)) return;
        
        try {
            const res = await fetch(`/api/workspace/item/${item.id}`, {
                method: 'DELETE'
            });
            const data = await res.json();
            if (data.success) {
                await loadWorkspace();
                
                // 如果刪除的是當前打開的項目，清空編輯區
                if (currentItem.value && currentItem.value.id === item.id) {
                    closeCurrent();
                }
            } else {
                alert('刪除失敗: ' + data.error);
            }
        } catch (e) {
            alert('連線錯誤');
        }
    };

    // 移動項目
    const moveItem = async (itemId, newParentId) => {
        try {
            const res = await fetch(`/api/workspace/item/${itemId}/move`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ parent_id: newParentId })
            });
            const data = await res.json();
            if (data.success) {
                await loadWorkspace();
            } else {
                alert('移動失敗: ' + data.error);
            }
        } catch (e) {
            alert('連線錯誤');
        }
    };

    // 公開分享牌組
    const publishDeck = async (item) => {
        if (item.type !== 'deck') return;
        
        if (!confirm(`確定要公開分享「${item.name}」嗎？\n分享後其他人可以透過連結查看並複製此牌組。`)) return;
        
        try {
            const res = await fetch(`/api/workspace/item/${item.id}/publish`, {
                method: 'POST'
            });
            const data = await res.json();
            if (data.success) {
                // 複製連結到剪貼簿
                await navigator.clipboard.writeText(data.share_url);
                alert(`分享成功！\n\n分享連結已複製到剪貼簿：\n${data.share_url}`);
            } else {
                alert('分享失敗: ' + data.error);
            }
        } catch (e) {
            alert('連線錯誤');
        }
    };

    // === UI 操作 ===

    // 打開牌組
    const openDeck = async (item) => {
        if (item.type !== 'deck') return;
        
        // 如果有未儲存的變更，先儲存
        if (hasUnsavedChanges.value && currentItem.value) {
            await saveCurrentDeck();
        }
        
        // 載入牌組內容
        try {
            const res = await fetch(`/api/workspace/item/${item.id}`);
            const data = await res.json();
            if (data.success) {
                currentItem.value = data.item;
                currentDeckName.value = data.item.name;
                
                // 清空當前牌組並載入
                suppressAutoSave = true;
                deck.value.length = 0;
                const content = data.item.content || [];
                content.forEach(card => {
                    deck.value.push({
                        ...card,
                        uniqueId: Date.now() + Math.random().toString(36).substr(2, 9)
                    });
                });
                suppressAutoSave = false;
                
                hasUnsavedChanges.value = false;
                if (timelineActions && timelineActions.resetHistory) {
                    timelineActions.resetHistory();
                }
                await loadTimeline();
            }
        } catch (e) {
            suppressAutoSave = false;
            console.error("Open deck error:", e);
        }
    };

    // 切換資料夾展開/收合
    const toggleFolder = (folderId) => {
        if (expandedFolders.value.has(folderId)) {
            expandedFolders.value.delete(folderId);
        } else {
            expandedFolders.value.add(folderId);
        }
    };

    // 關閉當前項目
    const closeCurrent = () => {
        suppressAutoSave = true;
        currentItem.value = null;
        currentDeckName.value = '';
        deck.value.length = 0;
        suppressAutoSave = false;
        hasUnsavedChanges.value = false;
        timelineEntries.value = [];
    };

    // 新建牌組（保存當前牌組到工作區）
    const saveAsNewDeck = (parentId = null) => {
        wsModal.visible = true;
        wsModal.mode = 'create';
        wsModal.type = 'deck';
        wsModal.parentId = parentId;
        wsModal.itemId = null;
        wsModal.name = currentDeckName.value || '新牌組';
    };

    // 新建空白牌組
    const createNewDeck = (parentId = null) => {
        // 如果有未儲存的變更，先詢問
        if (hasUnsavedChanges.value && currentItem.value) {
            if (!confirm('當前牌組有未儲存的變更，是否放棄？')) return;
        }
        
        wsModal.visible = true;
        wsModal.mode = 'create';
        wsModal.type = 'deck';
        wsModal.parentId = parentId;
        wsModal.itemId = null;
        wsModal.name = '新牌組';
        
        // 清空當前牌組
        closeCurrent();
    };

    // 新建資料夾
    const createNewFolder = (parentId = null) => {
        wsModal.visible = true;
        wsModal.mode = 'createFolder';
        wsModal.type = 'folder';
        wsModal.parentId = parentId;
        wsModal.itemId = null;
        wsModal.name = '新資料夾';
    };

    // 打開重新命名對話框
    const openRenameModal = (item) => {
        wsModal.visible = true;
        wsModal.mode = 'rename';
        wsModal.itemId = item.id;
        wsModal.name = item.name;
        wsModal.type = item.type;
    };

    // 右鍵選單
    const openWsContextMenu = (e, item) => {
        e.preventDefault();
        e.stopPropagation();
        wsContextMenu.visible = true;
        wsContextMenu.x = e.clientX;
        wsContextMenu.y = e.clientY;
        wsContextMenu.item = item;
    };

    const closeWsContextMenu = () => {
        wsContextMenu.visible = false;
    };

    // 處理右鍵選單動作
    const handleWsContextAction = (action) => {
        const item = wsContextMenu.item;
        if (!item) return;
        
        switch (action) {
            case 'open':
                if (item.type === 'deck') openDeck(item);
                else toggleFolder(item.id);
                break;
            case 'rename':
                openRenameModal(item);
                break;
            case 'delete':
                deleteItem(item);
                break;
            case 'newDeck':
                createNewDeck(item.type === 'folder' ? item.id : null);
                break;
            case 'newFolder':
                createNewFolder(item.type === 'folder' ? item.id : null);
                break;
            case 'publish':
                publishDeck(item);
                break;
        }
        closeWsContextMenu();
    };

    // 更新樹狀結構中的項目
    const updateItemInTree = (itemId, updates) => {
        const findAndUpdate = (items) => {
            for (let item of items) {
                if (item.id === itemId) {
                    Object.assign(item, updates);
                    return true;
                }
                if (item.children && findAndUpdate(item.children)) {
                    return true;
                }
            }
            return false;
        };
        findAndUpdate(workspaceTree.value);
    };

    // 切換側邊欄顯示
    const toggleWorkspace = () => {
        workspaceVisible.value = !workspaceVisible.value;
    };

    // 拖曳支援
    const handleWsDragStart = (e, item) => {
        e.dataTransfer.setData('workspace-item', JSON.stringify(item));
        e.dataTransfer.effectAllowed = 'move';
    };

    const handleWsDragOver = (e, targetItem) => {
        if (targetItem && targetItem.type === 'folder') {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        }
    };

    const handleWsDrop = (e, targetItem) => {
        e.preventDefault();
        const raw = e.dataTransfer.getData('workspace-item');
        if (!raw) return;
        
        try {
            const draggedItem = JSON.parse(raw);
            const newParentId = targetItem ? (targetItem.type === 'folder' ? targetItem.id : targetItem.parent_id) : null;
            
            if (draggedItem.id !== newParentId) {
                moveItem(draggedItem.id, newParentId);
            }
        } catch (e) {
            console.error("Drop error:", e);
        }
    };

    // Modal 提交
    const submitWsModal = () => {
        if (wsModal.mode === 'rename') {
            renameItem();
        } else {
            createItem();
        }
    };

    return {
        // 狀態
        workspaceVisible,
        workspaceTree,
        workspaceLoading,
        currentItem,
        hasUnsavedChanges,
        timelineEntries,
        timelineLoading,
        timelineRestoring,
        wsContextMenu,
        wsModal,
        expandedFolders,
        isWorkspaceAvailable,
        
        // 方法
        loadWorkspace,
        saveCurrentDeck,
        loadTimeline,
        restoreTimeline,
        formatTimelineTime,
        openDeck,
        toggleFolder,
        closeCurrent,
        saveAsNewDeck,
        createNewDeck,
        createNewFolder,
        deleteItem,
        publishDeck,
        toggleWorkspace,
        
        // 右鍵選單
        openWsContextMenu,
        closeWsContextMenu,
        handleWsContextAction,
        
        // Modal
        submitWsModal,
        
        // 拖曳
        handleWsDragStart,
        handleWsDragOver,
        handleWsDrop
    };
}
