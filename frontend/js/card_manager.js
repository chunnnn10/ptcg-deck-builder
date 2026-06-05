// /Pokemon/public/js/card_manager.js

// 需要傳入 deck 相關的操作方法
function useCardManager(deckHandlers) {
    const { ref, reactive, onMounted, onUnmounted } = Vue;
    const { addToDeck, removeCardInstance, currentLang } = deckHandlers;

    // === 狀態 ===
    const searchQuery = ref('皮卡丘');
    const searchFilters = reactive({
        type: '',        // Pokémon, Trainer...
        element: '',     // Fire, Water...
        stage: '',       // Basic, Stage 1...
        regulation: ''   // standard, expanded, all
    });
    const showFilters = ref(false);
    const hasActiveFilters = computed(() => !!(searchFilters.type || searchFilters.element || searchFilters.stage || searchFilters.regulation));
    const searchResults = ref([]);
    const loading = ref(false);
    const showModal = ref(false);
    const selectedCard = ref({});
    const contextMenu = ref({ visible: false, x: 0, y: 0, card: null });
    
    // 解析度檢查
    const isLowResolution = ref(false);
    const resolutionInfo = ref("");

    // 新增卡牌
    const showAddCardModal = ref(false);
    const addCardMode = ref('new');
    const isSubmittingCard = ref(false);
    
    // === 核心資料結構更新 ===
    const newCardData = reactive({
        name: '', 
        id: '', 
        type: 'Pokémon', 
        subType: 'Basic', 
        hp: '', 
        element: 'Lightning',
        // 移除原本單一的 text，改為 skills 陣列
        skills: [], 
        imageFile: null, 
        sourceCardId: '', 
        sourceName: ''
    });

    const sourceSearchQuery = ref('');
    const sourceSearchResults = ref([]);
    const previewImage = ref(null);

    // 能量圖標映射
    const energyMap = {
        'Grass': 'https://asia.pokemon-card.com/various_images/energy/Grass.png',
        'Fire': 'https://asia.pokemon-card.com/various_images/energy/Fire.png',
        'Water': 'https://asia.pokemon-card.com/various_images/energy/Water.png',
        'Lightning': 'https://asia.pokemon-card.com/various_images/energy/Lightning.png',
        'Psychic': 'https://asia.pokemon-card.com/various_images/energy/Psychic.png',
        'Fighting': 'https://asia.pokemon-card.com/various_images/energy/Fighting.png',
        'Darkness': 'https://asia.pokemon-card.com/various_images/energy/Darkness.png',
        'Metal': 'https://asia.pokemon-card.com/various_images/energy/Metal.png',
        'Fairy': 'https://asia.pokemon-card.com/various_images/energy/Fairy.png',
        'Dragon': 'https://asia.pokemon-card.com/various_images/energy/Dragon.png',
        'Colorless': 'https://asia.pokemon-card.com/various_images/energy/Colorless.png'
    };
    const getEnergyIcon = (type) => energyMap[type] || energyMap['Colorless'];

    // === 技能編輯功能 ===
    
    // 新增一個空白技能
    const addSkill = () => {
        newCardData.skills.push({
            name: '',
            damage: '',
            cost: [], // 能量陣列
            effect: '',
            isAbility: false // 是否為特性
        });
    };

    // 移除技能
    const removeSkill = (index) => {
        newCardData.skills.splice(index, 1);
    };

    // 增加技能能量消耗
    const addSkillCost = (skillIndex, energyType) => {
        if (!newCardData.skills[skillIndex].cost) newCardData.skills[skillIndex].cost = [];
        newCardData.skills[skillIndex].cost.push(energyType);
    };

    // 移除技能最後一個能量消耗
    const removeLastSkillCost = (skillIndex) => {
        if (newCardData.skills[skillIndex].cost && newCardData.skills[skillIndex].cost.length > 0) {
            newCardData.skills[skillIndex].cost.pop();
        }
    };

    // 添加特殊規則 (Rule Box)
    const addRuleBox = (type) => {
        let ruleName = "";
        let ruleText = "";
        
        switch(type) {
            case 'ex':
                ruleName = "寶可夢ex規則";
                ruleText = "寶可夢ex【氣絕】時，對手獲得2張獎賞卡。";
                break;
            case 'V':
                ruleName = "寶可夢V規則";
                ruleText = "寶可夢V【氣絕】時，對手獲得2張獎賞卡。";
                break;
            case 'VMAX':
                ruleName = "寶可夢VMAX規則";
                ruleText = "寶可夢VMAX【氣絕】時，對手獲得3張獎賞卡。";
                break;
            case 'VSTAR':
                ruleName = "VSTAR規則";
                ruleText = "寶可夢VSTAR【氣絕】時，對手獲得2張獎賞卡。";
                break;
            case 'ACE SPEC':
                ruleName = "ACE SPEC規則";
                ruleText = "ACE SPEC卡在牌組中只能有1張。";
                break;
        }

        // 檢查是否已存在類似規則，避免重複
        const exists = newCardData.skills.find(s => s.name === ruleName);
        if (!exists) {
            newCardData.skills.push({
                name: ruleName,
                damage: '',
                cost: [],
                effect: ruleText,
                isAbility: false,
                isRule: true // 標記為規則
            });
        }
    };

    // === API 搜尋 ===
    const searchCards = async () => {
        // [修改] 允許僅有過濾條件時搜尋
        if (!searchQuery.value.trim() && !searchFilters.type && !searchFilters.element && !searchFilters.stage) return;
        
        loading.value = true;
        searchResults.value = [];
        try {
            const params = new URLSearchParams();
            if (searchQuery.value.trim()) params.append('q', searchQuery.value.trim());
            if (searchFilters.type) params.append('type', searchFilters.type);
            if (searchFilters.element) params.append('element', searchFilters.element);
            if (searchFilters.stage) params.append('stage', searchFilters.stage);
            if (searchFilters.regulation) params.append('regulation', searchFilters.regulation);

            const apiPath = currentLang && currentLang.value === 'jp' ? '/api/jp/search' : '/api/search';
            const res = await fetch(`${apiPath}?${params.toString()}`);
            if (!res.ok) throw new Error("Server error");
            const data = await res.json();
            searchResults.value = Array.isArray(data) ? data : [];
        } catch (e) {
            console.error(e);
        } finally {
            loading.value = false;
        }
    };

    // === 詳情彈窗 ===
    const modalContext = ref('search'); // 'search' | 'deck'
    const cardVariants = ref({ tw: null, jp: null });

    const cardNeedsDetailRefresh = (card) => {
        if (!card) return false;
        const cid = String(card.card_id || card.id || '');
        if (!cid || cid.startsWith('jp')) return false;
        const usefulSkills = Array.isArray(card.skills)
            ? card.skills.filter(s => s && (s.name || s.effect || s.text || s.damage || (s.cost && s.cost.length)))
            : [];
        const hasText = !!(card.text || card.effect || card.description);
        return !card.image_url || (!usefulSkills.length && !hasText);
    };

    const hydrateCardDetail = async (card) => {
        const cid = card && (card.card_id || card.id);
        if (!cid) return card;
        try {
            const isJp = String(cid).startsWith('jp') || card.language === 'jp';
            const apiPath = isJp ? '/api/jp/cards/batch' : '/api/cards/batch';
            const res = await fetch(apiPath, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: [cid] })
            });
            if (!res.ok) return card;
            const data = await res.json();
            if (Array.isArray(data) && data.length > 0) {
                return { ...card, ...data[0] };
            }
        } catch (e) {
            console.error("Card hydrate error:", e);
        }
        return card;
    };

    const openModal = async (c, context = 'search') => {
        selectedCard.value = c;
        modalContext.value = context;
        showModal.value = true;
        isLowResolution.value = false;
        cardVariants.value = { tw: null, jp: null };

        selectedCard.value = await hydrateCardDetail(c);
        const cid = selectedCard.value.card_id || selectedCard.value.id || c.card_id || c.id;

        // [新增] 若是查看牌組，從後端同步最新資料 (補齊 set_code/set_number)
        if (context === 'deck' && cid) {
            try {
                const res = await fetch(`/api/search?q=${encodeURIComponent(cid)}&full=1`);
                const data = await res.json();
                if (Array.isArray(data) && data.length > 0) {
                    const fresh = data.find(item => item.card_id === cid || item.id === cid);
                    if (fresh) {
                         selectedCard.value = { ...c, ...fresh };
                    }
                }
            } catch(e) { console.error("Auto refresh card error:", e); }
        }

        // [新增] 查詢跨語言版本
        if (cardNeedsDetailRefresh(selectedCard.value)) {
            const refreshId = selectedCard.value.card_id || selectedCard.value.id || cid;
            try {
                const refreshRes = await fetch(`/api/card/refresh/${encodeURIComponent(refreshId)}`, { method: 'POST' });
                if (refreshRes.ok) {
                    const refreshData = await refreshRes.json();
                    if (refreshData.success && refreshData.card) {
                        selectedCard.value = { ...selectedCard.value, ...refreshData.card };
                    }
                }
            } catch(e) { console.error("Card detail refresh error:", e); }
        }

        if (cid) {
            try {
                const vRes = await fetch(`/api/card/variants/${cid}`);
                if (vRes.ok) {
                    cardVariants.value = await vRes.json();
                }
            } catch(e) { /* silent */ }
        }
    };
    const closeModal = () => showModal.value = false;

    const switchCardVariant = async (cardId) => {
        // 切換到另一個語言版本，發送批次查詢獲取完整資料後顯示
        showModal.value = false;
        try {
            const apiPath = cardId.startsWith('jp') ? '/api/jp/cards/batch' : '/api/cards/batch';
            const res = await fetch(apiPath, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: [cardId] })
            });
            if (res.ok) {
                const data = await res.json();
                if (Array.isArray(data) && data.length > 0) {
                    openModal(data[0], 'search');
                }
            }
        } catch(e) { console.error("Variant switch error:", e); }
    };
    
    const searchSameName = (name) => {
        searchQuery.value = name;
        showModal.value = false;
        searchCards();
    };

    const handleImageError = (e) => {
        const img = e.target;
        if (img.src.includes("placehold.co")) return;
        if (img.src.includes("asia.pokemon-card.com") || img.dataset.officialFallback === '1') {
            img.src = "https://placehold.co/245x342?text=No+Image";
            return;
        }
        const srcParts = img.src.split('/');
        const fileName = srcParts[srcParts.length - 1];
        if (fileName && fileName.length > 0) {
            img.dataset.officialFallback = '1';
            img.src = `https://asia.pokemon-card.com/tw/card-img/${fileName}`;
        }
        else img.src = "https://placehold.co/245x342?text=No+Image";
    };

    const checkResolution = (e) => {
        if (e.target.naturalWidth < 300) {
            isLowResolution.value = true;
            resolutionInfo.value = `${e.target.naturalWidth}x`;
        } else {
            isLowResolution.value = false;
        }
    };

    // === 右鍵選單 ===
    const openContextMenu = (e, card) => {
        e.preventDefault();
        e.stopPropagation(); // [新增] 防止觸發 window click
        contextMenu.value = { visible: true, x: e.clientX, y: e.clientY, card: card };
    };
    
    const closeContextMenu = () => contextMenu.value.visible = false;
    
    const contextAction = (action) => {
        const card = contextMenu.value.card;
        if (!card) return;
        switch (action) {
            case 'delete': removeCardInstance(card.uniqueId); break;
            case 'copy': addToDeck(card); break;
            case 'search': searchQuery.value = card.name; searchCards(); break;
            case 'detail': openModal(card); break;
        }
        closeContextMenu();
    };

    // === 新增卡牌功能 ===
    const openAddCardModal = () => {
        newCardData.name = ''; newCardData.id = ''; newCardData.imageFile = null;
        newCardData.hp = ''; newCardData.sourceCardId = ''; 
        newCardData.sourceName = ''; 
        // 預設給一個空白技能
        newCardData.skills = [{name: '', damage: '', cost: [], effect: '', isAbility: false}];
        previewImage.value = null; sourceSearchQuery.value = '';
        showAddCardModal.value = true;
    };

    const handleFileUpload = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        newCardData.imageFile = file;
        const reader = new FileReader();
        reader.onload = (e) => previewImage.value = e.target.result;
        reader.readAsDataURL(file);
    };

    const searchSourceCard = async () => {
        if (!sourceSearchQuery.value.trim()) return;
        try {
            const res = await fetch(`/api/search?q=${encodeURIComponent(sourceSearchQuery.value)}`);
            const data = await res.json();
            sourceSearchResults.value = Array.isArray(data) ? data : [];
        } catch (e) { console.error(e); }
    };

    const selectSourceCard = (card) => {
        newCardData.sourceCardId = card.card_id || card.id;
        newCardData.sourceName = card.name;
        sourceSearchResults.value = [];
    };

    const submitNewCard = async () => {
        if (!newCardData.id) return alert("請輸入卡牌代號 (ID)");
        if (!newCardData.imageFile) return alert("請上傳圖片");

        isSubmittingCard.value = true;
        const formData = new FormData();
        formData.append('image', newCardData.imageFile);
        formData.append('card_id', newCardData.id);
        formData.append('mode', addCardMode.value);

        if (addCardMode.value === 'new') {
            formData.append('name', newCardData.name);
            formData.append('card_type', newCardData.type);
            formData.append('sub_type', newCardData.subType);
            // 現在無論什麼類型都允許傳送 HP (為了支援物品卡 HP)
            formData.append('hp', newCardData.hp);
            formData.append('element_type', newCardData.element);
            
            // 將複雜的技能物件轉換為 JSON 字串傳送
            formData.append('skills_data', JSON.stringify(newCardData.skills));
        } else {
            formData.append('source_card_id', newCardData.sourceCardId);
        }

        try {
            const res = await fetch('/api/card/add', { method: 'POST', body: formData });
            const data = await res.json();
            if (data.success) {
                alert("新增成功！");
                showAddCardModal.value = false;
                if (searchQuery.value) searchCards();
            } else {
                alert("新增失敗: " + data.error);
            }
        } catch (e) {
            alert("伺服器錯誤");
        } finally {
            isSubmittingCard.value = false;
        }
    };

    // 生命週期
    onMounted(() => {
        window.addEventListener('click', closeContextMenu);
        window.addEventListener('click', () => { sourceSearchResults.value = []; });
    });
    onUnmounted(() => window.removeEventListener('click', closeContextMenu));

    return {
        searchQuery, searchFilters, searchResults, loading, showModal, selectedCard, contextMenu,
        isLowResolution, resolutionInfo, getEnergyIcon,
        searchCards, openModal, closeModal, searchSameName, handleImageError, checkResolution,
        openContextMenu, contextAction,
        modalContext,
        cardVariants, switchCardVariant,
        showFilters, hasActiveFilters, // [新增]
        
        // Add Card
        showAddCardModal, addCardMode, isSubmittingCard, newCardData, sourceSearchQuery, sourceSearchResults, previewImage,
        openAddCardModal, handleFileUpload, searchSourceCard, selectSourceCard, submitNewCard,
        
        // Skills Edit
        addSkill, removeSkill, addSkillCost, removeLastSkillCost, addRuleBox
    };
}
