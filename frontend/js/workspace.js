// /Pokemon/public/js/workspace.js

function useWorkspace(deck, currentDeckName, isAuthenticated, timelineActions = null) {
    const { ref, reactive, computed, watch } = Vue;
    const deckSession = timelineActions && timelineActions.deckSession ? timelineActions.deckSession : null;

    const workspaceVisible = ref(true);
    const workspaceTree = ref([]);
    const workspaceLoading = ref(false);
    const legacyCurrentItem = ref(null);
    const legacyHasUnsavedChanges = ref(false);
    const autoSaveTimer = ref(null);
    const timelineEntries = ref([]);
    const timelineLoading = ref(false);
    const timelineRestoring = ref(false);
    let suppressAutoSave = false;
    let skipNextDeckWatch = false;

    const currentItem = computed({
        get() {
            if (deckSession && deckSession.activeWorkspaceItem) {
                return deckSession.activeWorkspaceItem.value || null;
            }
            return legacyCurrentItem.value;
        },
        set(value) {
            legacyCurrentItem.value = value;
            if (!deckSession || !deckSession.activeDeckTab || !deckSession.activeDeckTab.value) return;
            const tab = deckSession.activeDeckTab.value;
            if (value && value.type === "deck") {
                tab.source = "workspace";
                tab.workspaceItem = value;
                tab.workspaceItemId = value.id;
                tab.title = value.name || tab.title;
            } else if (tab.source === "workspace") {
                tab.source = "scratch";
                tab.workspaceItem = null;
                tab.workspaceItemId = null;
            }
        }
    });

    const hasUnsavedChanges = computed({
        get() {
            if (deckSession && deckSession.activeDeckTabDirty) {
                return !!deckSession.activeDeckTabDirty.value;
            }
            return legacyHasUnsavedChanges.value;
        },
        set(value) {
            if (deckSession && deckSession.activeDeckTab && deckSession.activeDeckTab.value) {
                deckSession.activeDeckTab.value.dirty = !!value;
            } else {
                legacyHasUnsavedChanges.value = !!value;
            }
        }
    });

    const wsContextMenu = reactive({
        visible: false,
        x: 0,
        y: 0,
        item: null
    });

    const wsModal = reactive({
        visible: false,
        mode: "create",
        parentId: null,
        itemId: null,
        name: "",
        type: "deck",
        content: null,
        bindActiveTab: false
    });

    const expandedFolders = ref(new Set());
    const isWorkspaceAvailable = computed(() => isAuthenticated.value);

    const cloneForSave = (cards) => JSON.parse(JSON.stringify(cards || []));
    const makeUniqueCards = (cards) => (cards || []).map(card => ({
        ...card,
        uniqueId: Date.now() + Math.random().toString(36).slice(2, 11)
    }));

    const activeTab = () => deckSession && deckSession.activeDeckTab ? deckSession.activeDeckTab.value : null;
    const isActiveTab = (tab) => !!(tab && deckSession && deckSession.activeDeckTabId && tab.id === deckSession.activeDeckTabId.value);

    const updateItemInTree = (itemId, updates) => {
        const walk = (items) => {
            for (const item of items) {
                if (item.id === itemId) {
                    Object.assign(item, updates);
                    return true;
                }
                if (item.children && walk(item.children)) return true;
            }
            return false;
        };
        walk(workspaceTree.value);
    };

    const loadWorkspace = async () => {
        if (!isAuthenticated.value) return;
        workspaceLoading.value = true;
        try {
            const res = await fetch("/api/workspace");
            const data = await res.json();
            if (data.success) workspaceTree.value = data.workspace || [];
        } catch (e) {
            console.error("Load workspace error:", e);
        } finally {
            workspaceLoading.value = false;
        }
    };

    const formatTimelineTime = (value) => {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleString("zh-TW", {
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit"
        });
    };

    const loadTimeline = async () => {
        const item = currentItem.value;
        if (!item || item.type !== "deck") {
            timelineEntries.value = [];
            return;
        }
        timelineLoading.value = true;
        try {
            const res = await fetch(`/api/workspace/item/${item.id}/timeline`);
            const data = await res.json();
            if (data.success) timelineEntries.value = data.timeline || [];
        } catch (e) {
            console.error("Load timeline error:", e);
        } finally {
            timelineLoading.value = false;
        }
    };

    const saveWorkspaceTab = async (targetTab = null) => {
        const tab = targetTab || activeTab();
        const tabbed = !!(deckSession && tab);
        const item = tabbed ? tab.workspaceItem : currentItem.value;
        const itemId = tabbed ? (tab.workspaceItemId || (item && item.id)) : (item && item.id);
        const cards = tabbed ? (tab.deck || []) : deck.value;
        const dirty = tabbed ? !!tab.dirty : !!hasUnsavedChanges.value;

        if (tabbed && tab.source !== "workspace") return true;
        if (!itemId) return false;
        if (!dirty) return true;

        try {
            const timelineAction = isActiveTab(tab) && timelineActions && timelineActions.consumeTimelineAction
                ? timelineActions.consumeTimelineAction()
                : "Save deck";
            const res = await fetch(`/api/workspace/item/${itemId}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    content: cloneForSave(cards),
                    timeline_action: timelineAction,
                    timeline_source: "editor"
                })
            });
            const data = await res.json();
            if (!data.success) {
                console.error("Workspace save failed:", data.error);
                return false;
            }

            if (tabbed) {
                tab.dirty = false;
                tab.workspaceItem = {
                    ...(tab.workspaceItem || {}),
                    ...(item || {}),
                    id: itemId,
                    type: "deck",
                    card_count: cards.length
                };
            } else {
                hasUnsavedChanges.value = false;
            }
            updateItemInTree(itemId, { card_count: cards.length });
            if ((!tabbed || isActiveTab(tab)) && data.timeline) await loadTimeline();
            return true;
        } catch (e) {
            console.error("Workspace save error:", e);
            return false;
        }
    };

    const autoSaveDeck = async () => saveWorkspaceTab(activeTab());

    const saveCurrentDeck = async () => {
        if (autoSaveTimer.value) {
            clearTimeout(autoSaveTimer.value);
            autoSaveTimer.value = null;
        }
        return await autoSaveDeck();
    };

    const flushActiveWorkspaceAutosave = async () => {
        if (autoSaveTimer.value) {
            clearTimeout(autoSaveTimer.value);
            autoSaveTimer.value = null;
        }
        const tab = activeTab();
        if (tab && tab.source === "workspace") return await saveWorkspaceTab(tab);
        if (!tab && currentItem.value) return await saveWorkspaceTab(null);
        return true;
    };

    const flushWorkspaceTabAutosave = async (tab) => saveWorkspaceTab(tab);

    watch(deck, () => {
        if (suppressAutoSave) return;
        if (skipNextDeckWatch) {
            skipNextDeckWatch = false;
            return;
        }

        const tab = activeTab();
        if (tab && tab.source !== "workspace") return;
        if (!tab && (!currentItem.value || currentItem.value.type !== "deck")) return;

        hasUnsavedChanges.value = true;
        if (autoSaveTimer.value) clearTimeout(autoSaveTimer.value);
        autoSaveTimer.value = setTimeout(() => {
            autoSaveTimer.value = null;
            autoSaveDeck();
        }, 2000);
    }, { deep: true });

    if (deckSession && deckSession.activeDeckTabId) {
        watch(deckSession.activeDeckTabId, () => {
            if (autoSaveTimer.value) {
                clearTimeout(autoSaveTimer.value);
                autoSaveTimer.value = null;
            }
            loadTimeline();
        });
    }

    const activateDeckTab = async (tabId) => {
        if (!deckSession || !deckSession.activateDeckTab) return false;
        if (deckSession.activeDeckTabId && deckSession.activeDeckTabId.value === tabId) return true;
        const saved = await flushActiveWorkspaceAutosave();
        if (!saved) {
            alert("Workspace autosave failed. The current tab is still open and marked unsaved.");
            return false;
        }
        skipNextDeckWatch = true;
        const switched = deckSession.activateDeckTab(tabId);
        if (!switched) skipNextDeckWatch = false;
        if (switched) await loadTimeline();
        return switched;
    };

    const closeDeckTab = async (tabId, options = {}) => {
        if (!deckSession || !deckSession.deckTabs || !deckSession.closeDeckTab) return false;
        const tab = deckSession.deckTabs.value.find(item => item && item.id === tabId);
        if (!tab) return false;

        if (tab.dirty && !options.force) {
            if (tab.source === "workspace") {
                if (isActiveTab(tab) && autoSaveTimer.value) {
                    clearTimeout(autoSaveTimer.value);
                    autoSaveTimer.value = null;
                }
                const saved = await flushWorkspaceTabAutosave(tab);
                if (!saved) {
                    const discard = confirm(`Save failed for "${tab.title}". Close this workspace tab and discard unsaved changes?`);
                    if (!discard) return false;
                }
            } else {
                const discard = confirm(`Close "${tab.title}" and discard unsaved changes?`);
                if (!discard) return false;
            }
        }

        if (isActiveTab(tab)) skipNextDeckWatch = true;
        const closed = deckSession.closeDeckTab(tabId, { ...options, force: true });
        if (!closed) skipNextDeckWatch = false;
        if (closed) await loadTimeline();
        return closed;
    };

    const restoreTimeline = async (entry) => {
        if (!currentItem.value || !entry || timelineRestoring.value) return;
        if (!confirm(`Restore "${entry.action || "this timeline entry"}"? The active deck tab will be replaced.`)) return;
        timelineRestoring.value = true;
        try {
            const res = await fetch(`/api/workspace/item/${currentItem.value.id}/timeline/${entry.id}/restore`, {
                method: "POST"
            });
            const data = await res.json();
            if (!data.success) {
                alert(data.error || "Timeline restore failed");
                return;
            }

            suppressAutoSave = true;
            const restored = makeUniqueCards(data.content || []);
            deck.value.length = 0;
            deck.value.push(...restored);
            suppressAutoSave = false;
            hasUnsavedChanges.value = false;
            updateItemInTree(currentItem.value.id, { card_count: deck.value.length });
            if (timelineActions && timelineActions.resetHistory) timelineActions.resetHistory();
            await loadTimeline();
        } catch (e) {
            suppressAutoSave = false;
            console.error("Restore timeline error:", e);
            alert("Timeline restore failed");
        } finally {
            suppressAutoSave = false;
            timelineRestoring.value = false;
        }
    };

    const openDeck = async (item) => {
        if (!item || item.type !== "deck") return;

        const existing = deckSession && deckSession.deckTabs
            ? deckSession.deckTabs.value.find(tab => tab && tab.source === "workspace" && tab.workspaceItemId === item.id)
            : null;
        if (existing) {
            await activateDeckTab(existing.id);
            return;
        }

        const saved = await flushActiveWorkspaceAutosave();
        if (!saved) {
            alert("Workspace autosave failed. The requested deck was not opened.");
            return;
        }

        try {
            const res = await fetch(`/api/workspace/item/${item.id}`);
            const data = await res.json();
            if (!data.success) {
                alert(data.error || "Workspace deck load failed");
                return;
            }

            const loadedItem = data.item;
            const content = loadedItem.content || [];
            suppressAutoSave = true;
            if (deckSession && deckSession.openWorkspaceTab) {
                deckSession.openWorkspaceTab(loadedItem, content);
            } else {
                currentItem.value = loadedItem;
                currentDeckName.value = loadedItem.name;
                deck.value.length = 0;
                deck.value.push(...makeUniqueCards(content));
            }
            suppressAutoSave = false;
            hasUnsavedChanges.value = false;
            if (timelineActions && timelineActions.resetHistory) timelineActions.resetHistory();
            await loadTimeline();
        } catch (e) {
            suppressAutoSave = false;
            console.error("Open deck error:", e);
            alert("Workspace deck load failed");
        }
    };

    const createItem = async () => {
        if (!wsModal.name.trim()) {
            alert("Please enter a name.");
            return;
        }

        try {
            const content = wsModal.type === "deck"
                ? cloneForSave(Array.isArray(wsModal.content) ? wsModal.content : deck.value)
                : [];
            const res = await fetch("/api/workspace/item", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: wsModal.name.trim(),
                    type: wsModal.type,
                    parent_id: wsModal.parentId,
                    content
                })
            });
            const data = await res.json();
            if (!data.success) {
                alert("Create failed: " + data.error);
                return;
            }

            await loadWorkspace();
            wsModal.visible = false;
            if (wsModal.parentId) expandedFolders.value.add(wsModal.parentId);

            if (wsModal.type === "deck") {
                const item = { ...data.item, content, card_count: content.length };
                if (wsModal.bindActiveTab && deckSession && deckSession.bindActiveTabToWorkspace) {
                    deckSession.bindActiveTabToWorkspace(item);
                    await loadTimeline();
                } else {
                    await openDeck(item);
                }
            }
        } catch (e) {
            console.error(e);
            alert("Create failed.");
        }
    };

    const renameItem = async () => {
        if (!wsModal.name.trim() || !wsModal.itemId) return;

        try {
            const res = await fetch(`/api/workspace/item/${wsModal.itemId}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: wsModal.name.trim() })
            });
            const data = await res.json();
            if (!data.success) {
                alert("Rename failed: " + data.error);
                return;
            }

            await loadWorkspace();
            wsModal.visible = false;
            if (deckSession && deckSession.deckTabs) {
                deckSession.deckTabs.value.filter(Boolean).forEach(tab => {
                    if (tab.workspaceItemId === wsModal.itemId) {
                        tab.title = wsModal.name.trim();
                        if (tab.workspaceItem) tab.workspaceItem.name = wsModal.name.trim();
                    }
                });
            } else if (currentItem.value && currentItem.value.id === wsModal.itemId) {
                currentItem.value.name = wsModal.name.trim();
                currentDeckName.value = wsModal.name.trim();
            }
        } catch (e) {
            console.error(e);
            alert("Rename failed.");
        }
    };

    const closeWorkspaceTabsForItem = (itemId) => {
        if (!deckSession || !deckSession.deckTabs || !deckSession.closeDeckTab) {
            if (currentItem.value && currentItem.value.id === itemId) closeCurrent();
            return;
        }
        const tabs = deckSession.deckTabs.value.filter(tab => tab && tab.workspaceItemId === itemId);
        tabs.forEach(tab => deckSession.closeDeckTab(tab.id, { force: true }));
    };

    const deleteItem = async (item) => {
        if (!item) return;
        const typeText = item.type === "folder" ? "folder" : "deck";
        if (!confirm(`Delete ${typeText} "${item.name}"?`)) return;

        try {
            const res = await fetch(`/api/workspace/item/${item.id}`, { method: "DELETE" });
            const data = await res.json();
            if (data.success) {
                closeWorkspaceTabsForItem(item.id);
                await loadWorkspace();
            } else {
                alert("Delete failed: " + data.error);
            }
        } catch (e) {
            console.error(e);
            alert("Delete failed.");
        }
    };

    const moveItem = async (itemId, newParentId) => {
        try {
            const res = await fetch(`/api/workspace/item/${itemId}/move`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ parent_id: newParentId })
            });
            const data = await res.json();
            if (data.success) await loadWorkspace();
            else alert("Move failed: " + data.error);
        } catch (e) {
            console.error(e);
            alert("Move failed.");
        }
    };

    const publishDeck = async (item) => {
        if (!item || item.type !== "deck") return;
        if (!confirm(`Publish "${item.name}"?`)) return;

        try {
            const res = await fetch(`/api/workspace/item/${item.id}/publish`, { method: "POST" });
            const data = await res.json();
            if (data.success) {
                await navigator.clipboard.writeText(data.share_url);
                alert(`Published. Share link copied:\n${data.share_url}`);
            } else {
                alert("Publish failed: " + data.error);
            }
        } catch (e) {
            console.error(e);
            alert("Publish failed.");
        }
    };

    const toggleFolder = (folderId) => {
        if (expandedFolders.value.has(folderId)) expandedFolders.value.delete(folderId);
        else expandedFolders.value.add(folderId);
    };

    const closeCurrent = async () => {
        if (deckSession && deckSession.activeDeckTabId) {
            await closeDeckTab(deckSession.activeDeckTabId.value);
            return;
        }
        suppressAutoSave = true;
        currentItem.value = null;
        currentDeckName.value = "";
        deck.value.length = 0;
        suppressAutoSave = false;
        hasUnsavedChanges.value = false;
        timelineEntries.value = [];
    };

    const saveAsNewDeck = (parentId = null) => {
        wsModal.visible = true;
        wsModal.mode = "create";
        wsModal.type = "deck";
        wsModal.parentId = parentId;
        wsModal.itemId = null;
        wsModal.name = currentDeckName.value || "Untitled Deck";
        wsModal.content = cloneForSave(deck.value);
        wsModal.bindActiveTab = true;
    };

    const saveActiveTabAsWorkspace = (parentId = null) => saveAsNewDeck(parentId);

    const createNewDeck = (parentId = null) => {
        wsModal.visible = true;
        wsModal.mode = "create";
        wsModal.type = "deck";
        wsModal.parentId = parentId;
        wsModal.itemId = null;
        wsModal.name = "Untitled Deck";
        wsModal.content = [];
        wsModal.bindActiveTab = false;
    };

    const createNewFolder = (parentId = null) => {
        wsModal.visible = true;
        wsModal.mode = "createFolder";
        wsModal.type = "folder";
        wsModal.parentId = parentId;
        wsModal.itemId = null;
        wsModal.name = "New Folder";
        wsModal.content = null;
        wsModal.bindActiveTab = false;
    };

    const openRenameModal = (item) => {
        wsModal.visible = true;
        wsModal.mode = "rename";
        wsModal.itemId = item.id;
        wsModal.name = item.name;
        wsModal.type = item.type;
        wsModal.content = null;
        wsModal.bindActiveTab = false;
    };

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

    const handleWsContextAction = (action) => {
        const item = wsContextMenu.item;
        if (!item && action !== "newDeck" && action !== "newFolder") return;

        switch (action) {
            case "open":
                if (item.type === "deck") openDeck(item);
                else toggleFolder(item.id);
                break;
            case "rename":
                openRenameModal(item);
                break;
            case "delete":
                deleteItem(item);
                break;
            case "newDeck":
                createNewDeck(item && item.type === "folder" ? item.id : null);
                break;
            case "newFolder":
                createNewFolder(item && item.type === "folder" ? item.id : null);
                break;
            case "publish":
                publishDeck(item);
                break;
        }
        closeWsContextMenu();
    };

    const toggleWorkspace = () => {
        workspaceVisible.value = !workspaceVisible.value;
    };

    const handleWsDragStart = (e, item) => {
        e.dataTransfer.setData("workspace-item", JSON.stringify(item));
        e.dataTransfer.effectAllowed = "move";
    };

    const handleWsDragOver = (e, targetItem) => {
        if (!targetItem || targetItem.type === "folder") {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
        }
    };

    const handleWsDrop = (e, targetItem) => {
        e.preventDefault();
        const raw = e.dataTransfer.getData("workspace-item");
        if (!raw) return;
        try {
            const draggedItem = JSON.parse(raw);
            const newParentId = targetItem
                ? (targetItem.type === "folder" ? targetItem.id : targetItem.parent_id)
                : null;
            if (draggedItem.id !== newParentId) moveItem(draggedItem.id, newParentId);
        } catch (err) {
            console.error("Workspace drop error:", err);
        }
    };

    const submitWsModal = () => {
        if (wsModal.mode === "rename") renameItem();
        else createItem();
    };

    return {
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
        loadWorkspace,
        saveCurrentDeck,
        saveActiveTabAsWorkspace,
        flushActiveWorkspaceAutosave,
        flushWorkspaceTabAutosave,
        loadTimeline,
        restoreTimeline,
        formatTimelineTime,
        openDeck,
        activateDeckTab,
        closeDeckTab,
        toggleFolder,
        closeCurrent,
        saveAsNewDeck,
        createNewDeck,
        createNewFolder,
        deleteItem,
        publishDeck,
        toggleWorkspace,
        openWsContextMenu,
        closeWsContextMenu,
        handleWsContextAction,
        submitWsModal,
        handleWsDragStart,
        handleWsDragOver,
        handleWsDrop
    };
}
