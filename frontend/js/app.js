// /Pokemon/public/js/app.js
const { createApp, onMounted, onUnmounted, ref, computed, watch } = Vue;

const WorkspaceTreeNode = {
    name: "WorkspaceTreeNode",
    template: "#workspace-tree-node-template",
    delimiters: ["[[", "]]"],
    props: {
        item: { type: Object, required: true },
        depth: { type: Number, default: 0 },
        currentItem: { type: Object, default: null },
        expandedFolders: { type: Set, required: true },
        hasUnsavedChanges: { type: Boolean, default: false }
    },
    emits: ["toggle-folder", "open-deck", "context-menu", "drag-start", "drag-over", "drop"],
    setup(props, { emit }) {
        const isDragOver = ref(false);
        const isExpanded = computed(() => props.expandedFolders.has(props.item.id));

        const handleClick = () => props.item.type === "folder"
            ? emit("toggle-folder", props.item.id)
            : emit("open-deck", props.item);
        const handleDoubleClick = () => {
            if (props.item.type === "deck") emit("open-deck", props.item);
        };
        const onDragOver = (e) => {
            if (props.item.type === "folder") {
                isDragOver.value = true;
                emit("drag-over", e, props.item);
            }
        };
        const onDragLeave = () => { isDragOver.value = false; };
        const onDrop = (e) => {
            isDragOver.value = false;
            emit("drop", e, props.item);
        };

        return { isDragOver, isExpanded, handleClick, handleDoubleClick, onDragOver, onDragLeave, onDrop };
    }
};

createApp({
    delimiters: ["[[", "]]"],
    components: {
        "workspace-tree-node": WorkspaceTreeNode
    },
    setup() {
        const auth = useAuth();
        const deckManager = useDeckManager();
        const currentLang = ref("tw");
        const showMenu = ref(false);

        const isDesktop = ref(window.innerWidth >= 768);
        const activeActivity = ref("workspace");
        const sidePanelVisible = ref(true);
        const sidePanelWidth = ref(Number(localStorage.getItem("ptcg.sidePanelWidth")) || 320);
        const mobileTab = ref("deck");
        const isResizingSidePanel = ref(false);

        const clampPanelWidth = (width) => Math.max(260, Math.min(520, width));
        const workspaceVisible = computed(() => sidePanelVisible.value && activeActivity.value === "workspace");

        const openActivity = (activity) => {
            activeActivity.value = activity;
            sidePanelVisible.value = true;
            if (activity === "ai" && aiAssistant) aiAssistant.openAIPanel();
        };

        const toggleActivity = (activity) => {
            if (activeActivity.value === activity && sidePanelVisible.value) {
                sidePanelVisible.value = false;
            } else {
                openActivity(activity);
            }
        };

        const toggleWorkspace = () => toggleActivity("workspace");

        const closeSidePanel = () => {
            sidePanelVisible.value = false;
        };

        const startSidePanelResize = (event) => {
            if (!isDesktop.value) return;
            event.preventDefault();
            isResizingSidePanel.value = true;
            const startX = event.clientX;
            const startWidth = sidePanelWidth.value;
            const onMove = (moveEvent) => {
                sidePanelWidth.value = clampPanelWidth(startWidth + moveEvent.clientX - startX);
            };
            const onUp = () => {
                isResizingSidePanel.value = false;
                localStorage.setItem("ptcg.sidePanelWidth", String(sidePanelWidth.value));
                window.removeEventListener("mousemove", onMove);
                window.removeEventListener("mouseup", onUp);
            };
            window.addEventListener("mousemove", onMove);
            window.addEventListener("mouseup", onUp);
        };

        const cardManager = useCardManager({
            addToDeck: deckManager.addToDeck,
            removeCardInstance: deckManager.removeCardInstance,
            currentLang
        });

        const simulation = useSimulation(deckManager.deck, deckManager.isStandardMode);

        const workspace = useWorkspace(
            deckManager.deck,
            deckManager.currentDeckName,
            auth.isAuthenticated,
            {
                consumeTimelineAction: deckManager.consumeTimelineAction,
                resetHistory: deckManager.resetHistory,
                deckSession: deckManager
            }
        );

        const ioManager = useIOManager(
            deckManager.deck,
            deckManager.addToDeck,
            deckManager.currentDeckName,
            {
                hasItem: () => !!workspace.currentItem.value,
                save: workspace.saveCurrentDeck,
                close: workspace.closeCurrent,
                markTimelineAction: deckManager.markTimelineAction,
                withTimelineBatch: deckManager.withTimelineBatch,
                openImportedDeckTab: deckManager.openImportedDeckTab
            }
        );

        const adminFunction = useAdminUpdate();
        const aiAssistant = useAIAssistant(
            (card, context) => cardManager.openModal(card, context),
            {
                getDeck: () => deckManager.deck.value,
                getDeckTabs: () => deckManager.deckTabs.value,
                getActiveDeckTabId: () => deckManager.activeDeckTabId.value,
                addToDeck: deckManager.addToDeck,
                removeCardInstance: deckManager.removeCardInstance,
                openImportedDeckTab: (payload) => deckManager.openImportedDeckTab({
                    source: "ai",
                    ...(payload || {})
                }),
                replaceDeck: (cards, name) => deckManager.openImportedDeckTab({
                    source: "ai",
                    title: name || "AI 推薦牌表",
                    cards: cards || [],
                    meta: { source: "ai" }
                }),
                markTimelineAction: deckManager.markTimelineAction,
                withTimelineBatch: deckManager.withTimelineBatch,
                hasWorkspaceItem: () => !!workspace.currentItem.value,
                getWorkspaceItemId: () => workspace.currentItem.value ? workspace.currentItem.value.id : null,
                getLanguage: () => currentLang.value
            }
        );

        const openAIPanel = () => openActivity("ai");
        const closeAIPanel = () => {
            aiAssistant.closeAIPanel();
            if (activeActivity.value === "ai") sidePanelVisible.value = false;
        };
        const toggleAIPanel = () => toggleActivity("ai");

        const handleGlobalClick = () => {
            cardManager.contextMenu.value.visible = false;
            workspace.wsContextMenu.visible = false;
            deckManager.closeDeckContextMenu();
            showMenu.value = false;
            auth.showUserMenu.value = false;
        };

        const originalHandleCardClick = deckManager.handleCardClick;
        const handleCardClick = (card) => {
            originalHandleCardClick(card, (c) => cardManager.openModal(c, "deck"));
        };

        const loadPublicDeck = (id) => ioManager.loadPublicDeck(id);
        const goHome = () => window.location.href = "/";

        const handleResize = () => {
            isDesktop.value = window.innerWidth >= 768;
            if (isDesktop.value) {
                mobileTab.value = "deck";
            }
        };

        watch(activeActivity, (activity) => {
            if (activity === "ai") aiAssistant.openAIPanel();
        });

        onMounted(async () => {
            window.addEventListener("resize", handleResize);
            window.addEventListener("click", handleGlobalClick);
            window.addEventListener("contextmenu", handleGlobalClick);
            window.addEventListener("keydown", handleKeydown);

            const match = window.location.pathname.match(/\/card\/([A-Z0-9]{6})/);
            if (match) {
                const deckId = match[1];
                cardManager.loading.value = true;
                try {
                    const res = await fetch(`/api/deck/${deckId}`);
                    const data = await res.json();
                    if (data.success) {
                        deckManager.openImportedDeckTab({
                            source: "public",
                            title: data.name || "公開牌組",
                            cards: data.deck || [],
                            meta: { deck_id: deckId, is_public: !!data.is_public }
                        });
                        ioManager.saveDeckName.value = data.name || "";
                        if (data.is_public) ioManager.saveIsPublic.value = true;
                    } else {
                        alert("找不到牌組。");
                    }
                } catch (e) {
                    console.error(e);
                    alert("牌組載入失敗。");
                } finally {
                    cardManager.loading.value = false;
                }
            }

            if (auth.isAuthenticated.value) workspace.loadWorkspace();
        });

        onUnmounted(() => {
            window.removeEventListener("resize", handleResize);
            window.removeEventListener("click", handleGlobalClick);
            window.removeEventListener("contextmenu", handleGlobalClick);
            window.removeEventListener("keydown", handleKeydown);
        });

        watch(auth.isAuthenticated, (newVal) => {
            if (newVal) workspace.loadWorkspace();
        });

        function handleKeydown(e) {
            if ((e.ctrlKey || e.metaKey) && e.key === "z") {
                e.preventDefault();
                if (e.shiftKey) deckManager.redo();
                else deckManager.undo();
            } else if ((e.ctrlKey || e.metaKey) && e.key === "y") {
                e.preventDefault();
                deckManager.redo();
            }
        }

        return {
            currentLang,
            ...auth,
            ...deckManager,
            ...cardManager,
            ...simulation,
            ...ioManager,
            ...adminFunction,
            ...aiAssistant,
            ...workspace,
            workspaceVisible,
            toggleWorkspace,
            openAIPanel,
            closeAIPanel,
            toggleAIPanel,
            showMenu,
            handleCardClick,
            loadPublicDeck,
            goHome,
            handleGlobalClick,
            mobileTab,
            isDesktop,
            activeActivity,
            sidePanelVisible,
            sidePanelWidth,
            isResizingSidePanel,
            openActivity,
            toggleActivity,
            closeSidePanel,
            startSidePanelResize
        };
    }
}).mount("#app");
