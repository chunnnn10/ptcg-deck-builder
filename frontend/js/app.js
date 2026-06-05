// /Pokemon/public/js/app.js
const { createApp, onMounted, ref, computed, reactive } = Vue;

// 樹狀節點組件
const WorkspaceTreeNode = {
    name: 'WorkspaceTreeNode',
    template: '#workspace-tree-node-template',
    delimiters: ['[[', ']]'],
    props: {
        item: { type: Object, required: true },
        depth: { type: Number, default: 0 },
        currentItem: { type: Object, default: null },
        expandedFolders: { type: Set, required: true },
        hasUnsavedChanges: { type: Boolean, default: false }
    },
    emits: ['toggle-folder', 'open-deck', 'context-menu', 'drag-start', 'drag-over', 'drop'],
    setup(props, { emit }) {
        const isDragOver = ref(false);
        const isExpanded = computed(() => props.expandedFolders.has(props.item.id));
        
        const handleClick = () => props.item.type === 'folder' ? emit('toggle-folder', props.item.id) : emit('open-deck', props.item);
        const handleDoubleClick = () => { if (props.item.type === 'deck') emit('open-deck', props.item); };
        
        const onDragOver = (e) => {
            if (props.item.type === 'folder') {
                isDragOver.value = true;
                emit('drag-over', e, props.item);
            }
        };
        const onDragLeave = () => isDragOver.value = false;
        const onDrop = (e) => {
            isDragOver.value = false;
            emit('drop', e, props.item);
        };
        
        return { isDragOver, isExpanded, handleClick, handleDoubleClick, onDragOver, onDragLeave, onDrop };
    }
};

createApp({
    delimiters: ['[[', ']]'],
    components: {
        'workspace-tree-node': WorkspaceTreeNode
    },
    setup() {
        const auth = useAuth();
        
        // [修改] 這裡不要傳入 store，且只呼叫一次
        const deckManager = useDeckManager(); 
        
        // Mobile Tab State
        const mobileTab = ref('search'); // 'search' | 'deck'
        const isDesktop = ref(window.innerWidth >= 768);

        window.addEventListener('resize', () => {
            isDesktop.value = window.innerWidth >= 768;
        });
        
        const layout = reactive({ mobileTab: 'search' }); // Unused, keeping clean logic

        // 語言狀態 (tw / jp)
        const currentLang = ref('tw');

        const cardManager = useCardManager({
            addToDeck: deckManager.addToDeck,
            removeCardInstance: deckManager.removeCardInstance,
            currentLang: currentLang
        });

        const simulation = useSimulation(deckManager.deck, deckManager.isStandardMode);

        const workspace = useWorkspace(
            deckManager.deck,
            deckManager.currentDeckName,
            auth.isAuthenticated,
            {
                consumeTimelineAction: deckManager.consumeTimelineAction,
                resetHistory: deckManager.resetHistory
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
                withTimelineBatch: deckManager.withTimelineBatch
            }
        );

        const adminFunction = useAdminUpdate();
        const aiAssistant = useAIAssistant((card, context) => cardManager.openModal(card, context));

        const showMenu = ref(false);

        // 處理全域點擊，關閉所有右鍵選單
        const handleGlobalClick = () => {
            cardManager.contextMenu.value.visible = false;
            workspace.wsContextMenu.visible = false;
            deckManager.closeDeckContextMenu();
            showMenu.value = false; // 順便處理頂部功能選單
            auth.showUserMenu.value = false; // 順便處理用戶選單
        };

        const originalHandleCardClick = deckManager.handleCardClick;
        const handleCardClick = (card) => {
            originalHandleCardClick(card, (c) => cardManager.openModal(c, 'deck'));
        };

        const loadPublicDeck = (id) => {
            ioManager.loadPublicDeck(id, (newDeckData, newName) => {
                deckManager.deck.value = newDeckData.map(c => ({...c, uniqueId: Date.now() + Math.random().toString(36)}));
                deckManager.currentDeckName.value = newName;
                ioManager.saveDeckName.value = newName;
                workspace.currentItem.value = null;
            });
        };

        const goHome = () => window.location.href = '/';

        onMounted(async () => {
            const path = window.location.pathname;
            const match = path.match(/\/card\/([A-Z0-9]{6})/);
            // [新增] 註冊全域點擊事件
            window.addEventListener('click', handleGlobalClick);
            // [新增] 註冊右鍵事件 (避免在一個地方右鍵後，去另一個地方右鍵導致兩個選單重疊)
            window.addEventListener('contextmenu', handleGlobalClick);
            
            // [新增] 鍵盤快捷鍵 (Undo/Redo)
            window.addEventListener('keydown', (e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
                    e.preventDefault();
                    if (e.shiftKey) deckManager.redo();
                    else deckManager.undo();
                } else if ((e.ctrlKey || e.metaKey) && e.key === 'y') {
                    e.preventDefault();
                    deckManager.redo();
                }
            });
            
            if (match) {
                const deckId = match[1];
                cardManager.loading.value = true;
                try {
                    const res = await fetch(`/api/deck/${deckId}`);
                    const data = await res.json();
                    if (data.success) {
                        deckManager.deck.value = data.deck.map(c => ({...c, uniqueId: Date.now() + Math.random().toString(36)}));
                        deckManager.currentDeckName.value = data.name;
                        ioManager.saveDeckName.value = data.name;
                        if (data.is_public) ioManager.saveIsPublic.value = true;
                    } else {
                        alert("找不到該牌組，可能已被刪除。");
                    }
                } catch (e) {
                    console.error(e);
                    alert("載入牌組失敗");
                } finally {
                    cardManager.loading.value = false;
                }
            }
            
            if (auth.isAuthenticated.value) {
                workspace.loadWorkspace();
            }
        });

        Vue.watch(auth.isAuthenticated, (newVal) => {
            if (newVal) {
                workspace.loadWorkspace();
            }
        });

        // [新增] 卸載時移除監聽
        Vue.onUnmounted(() => {
            window.removeEventListener('click', handleGlobalClick);
            window.removeEventListener('contextmenu', handleGlobalClick);
        });

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
            showMenu,
            handleCardClick, 
            loadPublicDeck,   
            goHome,
            handleGlobalClick, // 回傳給 HTML body 使用
            mobileTab,
            isDesktop
        };
    }
}).mount('#app');
