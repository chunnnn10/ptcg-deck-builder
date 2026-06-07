function useAIAssistant(openCardModal, options = {}) {
    const { ref, computed, nextTick } = Vue;

    const aiPanelOpen = ref(false);
    const aiMessages = ref([
        {
            role: 'assistant',
            content: '我是 PTCG Agent。你可以請我查標準 H/I/J 卡池、參考 Limitless Meta，或提出可套用的牌組修改。'
        }
    ]);
    const aiInput = ref('');
    const aiLoading = ref(false);
    const aiError = ref('');
    const aiLastCards = ref([]);
    const aiToolSteps = ref([]);
    const aiDeepThink = ref(false);
    const aiLoadingMessage = ref('');
    const aiReferencedTabs = ref([]);
    const aiReferencedCards = ref([]);
    const aiTabPickerOpen = ref(false);
    const aiDropActive = ref(false);
    const pendingAIDeckImport = ref(null);
    let aiLoadingTimer = null;
    let aiPollTimer = null;

    const getDeck = () => {
        if (typeof options.getDeck === 'function') return options.getDeck() || [];
        return [];
    };

    const getDeckTabs = () => {
        if (typeof options.getDeckTabs === 'function') return options.getDeckTabs() || [];
        return [];
    };

    const getActiveDeckTabId = () => {
        if (typeof options.getActiveDeckTabId === 'function') return options.getActiveDeckTabId();
        return null;
    };

    const availableAITabs = computed(() => getDeckTabs()
        .filter(tab => tab && tab.id)
        .map(tab => ({
            id: tab.id,
            title: tab.title || 'Untitled Deck',
            source: tab.source || 'scratch',
            count: Array.isArray(tab.deck) ? tab.deck.length : 0,
            active: tab.id === getActiveDeckTabId()
        })));

    const compactCard = (card) => {
        if (!card || typeof card !== 'object') return null;
        const cardId = card.card_id || card.id || '';
        return {
            card_id: cardId,
            id: cardId,
            name: card.name || card.card_name || card.jp_card_name || card.japanese_name || card.chinese_name || '',
            card_name: card.card_name || card.name || '',
            language: card.language || '',
            card_type: card.card_type || '',
            sub_type: card.sub_type || '',
            set_code: card.set_code || '',
            set_number: card.set_number || '',
            regulation_mark: card.regulation_mark || '',
            image_url: card.image_url || card.image || '',
            count: card.count || undefined,
            skills: card.skills || undefined,
            description: card.description || ''
        };
    };

    const compactDeck = (deck) => (Array.isArray(deck) ? deck : [])
        .map(compactCard)
        .filter(Boolean);

    const cardMention = (card) => {
        const name = card && (card.name || card.card_name || card.jp_card_name || card.japanese_name || card.chinese_name) || 'card';
        const setNumber = String(card && card.set_number || '').trim();
        const cardId = String(card && (card.card_id || card.id) || '').trim();
        let suffix = setNumber;
        if (!suffix && cardId) {
            const match = cardId.match(/(?:^|[-_])(\d{1,3}(?:\/\d{1,3})?)$/);
            suffix = match ? match[1] : cardId;
        }
        return `@${name}${suffix}`;
    };

    const insertAIReferenceText = (text) => {
        const current = aiInput.value.trimEnd();
        aiInput.value = current ? `${current} ${text}` : text;
        nextTick(() => {
            const el = document.getElementById('ai-input');
            if (el) el.focus();
        });
    };

    const addAITabReference = (tab) => {
        if (!tab || !tab.id) return;
        const source = getDeckTabs().find(item => item && item.id === tab.id) || tab;
        if (!aiReferencedTabs.value.some(item => item.id === source.id)) {
            aiReferencedTabs.value.push({
                id: source.id,
                title: source.title || 'Untitled Deck',
                source: source.source || 'scratch',
                count: Array.isArray(source.deck) ? source.deck.length : (source.count || 0)
            });
        }
        insertAIReferenceText(`@tab:${source.title || 'Untitled Deck'}`);
        aiTabPickerOpen.value = false;
    };

    const removeAITabReference = (tabId) => {
        aiReferencedTabs.value = aiReferencedTabs.value.filter(item => item.id !== tabId);
    };

    const addAICardReference = (card) => {
        const compact = compactCard(card);
        if (!compact || !compact.name) return;
        const key = `${compact.language || ''}:${compact.card_id || compact.name}:${compact.set_number || ''}`;
        if (!aiReferencedCards.value.some(item => item.key === key)) {
            aiReferencedCards.value.push({ key, mention: cardMention(card), card: compact });
        }
        insertAIReferenceText(cardMention(card));
    };

    const removeAICardReference = (key) => {
        aiReferencedCards.value = aiReferencedCards.value.filter(item => item.key !== key);
    };

    const referencedTabPayloads = () => aiReferencedTabs.value.map(ref => {
        const tab = getDeckTabs().find(item => item && item.id === ref.id);
        return {
            id: ref.id,
            title: (tab && tab.title) || ref.title,
            source: (tab && tab.source) || ref.source,
            count: tab && Array.isArray(tab.deck) ? tab.deck.length : ref.count,
            deck: compactDeck(tab && tab.deck)
        };
    });

    const referencedCardPayloads = () => aiReferencedCards.value
        .map(item => item.card)
        .filter(Boolean);

    const handleAIDragOver = (event) => {
        if (!event || !event.dataTransfer) return;
        event.preventDefault();
        aiDropActive.value = true;
        event.dataTransfer.dropEffect = 'copy';
    };

    const handleAIDragLeave = () => {
        aiDropActive.value = false;
    };

    const handleAIDrop = (event) => {
        aiDropActive.value = false;
        const raw = event && event.dataTransfer ? event.dataTransfer.getData('application/json') : '';
        if (!raw) return;
        try {
            const payload = JSON.parse(raw);
            if (payload && payload.card) addAICardReference(payload.card);
        } catch (e) {
            console.warn('AI drop parse failed', e);
        }
    };

    const escapeHtml = (value) => String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');

    const renderMarkdown = (value) => {
        let html = escapeHtml(value);
        html = html.replace(/^### (.*)$/gm, '<h3 class="font-bold text-indigo-200 mt-3 mb-1">$1</h3>');
        html = html.replace(/^## (.*)$/gm, '<h2 class="font-bold text-indigo-100 mt-3 mb-1">$1</h2>');
        html = html.replace(/^# (.*)$/gm, '<h1 class="font-bold text-white mt-3 mb-1">$1</h1>');
        html = html.replace(/\*\*(.*?)\*\*/g, '<strong class="font-bold text-white">$1</strong>');
        html = html.replace(/`([^`]+)`/g, '<code class="px-1 py-0.5 rounded bg-gray-950 text-indigo-200">$1</code>');
        html = html.replace(/^\s*[-*] (.*)$/gm, '<li>$1</li>');
        html = html.replace(/(<li>.*<\/li>)(\n<li>.*<\/li>)*/gs, (match) => `<ul class="list-disc pl-5 space-y-1">${match}</ul>`);
        html = html.replace(/\n{2,}/g, '</p><p>');
        html = html.replace(/\n/g, '<br>');
        return `<p>${html}</p>`;
    };

    const scrollAIMessages = () => {
        nextTick(() => {
            const el = document.getElementById('ai-assistant-messages');
            if (el) el.scrollTop = el.scrollHeight;
        });
    };

    const openAIPanel = () => {
        aiPanelOpen.value = true;
        scrollAIMessages();
    };

    const closeAIPanel = () => {
        aiPanelOpen.value = false;
        aiTabPickerOpen.value = false;
    };

    const toggleAIPanel = () => {
        aiPanelOpen.value = !aiPanelOpen.value;
        if (aiPanelOpen.value) scrollAIMessages();
    };

    const loadingSequence = () => aiDeepThink.value
        ? [
            '正在拆解任務與牌組目標',
            '正在搜尋 H/I/J 標準卡池',
            '正在搜尋 Limitless Meta',
            '正在讀取具體上位牌表',
            '正在比較構築差異',
            '正在整理可視覺化牌組建議'
        ]
        : [
            '正在解析問題',
            '正在搜尋標準卡池',
            '正在檢查 Meta 索引',
            '正在整理 JSON 回覆'
        ];

    const startLoadingProgress = () => {
        stopLoadingProgress();
        const sequence = loadingSequence();
        let index = 0;
        const buildSteps = () => sequence.map((message, idx) => ({
            status: idx < index ? 'done' : (idx === index ? 'running' : 'pending'),
            message
        }));
        aiLoadingMessage.value = sequence[index];
        aiToolSteps.value = buildSteps();
        aiLoadingTimer = window.setInterval(() => {
            index = Math.min(index + 1, sequence.length - 1);
            aiLoadingMessage.value = sequence[index];
            aiToolSteps.value = buildSteps();
        }, aiDeepThink.value ? 3200 : 2400);
    };

    const stopLoadingProgress = () => {
        if (aiLoadingTimer) {
            window.clearInterval(aiLoadingTimer);
            aiLoadingTimer = null;
        }
    };

    const stopPolling = () => {
        if (aiPollTimer) {
            window.clearTimeout(aiPollTimer);
            aiPollTimer = null;
        }
    };

    const normalizeMessage = (data) => ({
        role: 'assistant',
        content: data.answer || '',
        cards: data.cards || [],
        meta_references: data.meta_references || [],
        decklists: data.decklists || [],
        deck_actions: data.deck_actions || [],
        deck_diff: data.deck_diff || null,
        tool_trace: data.tool_trace || data.steps || [],
        steps: data.steps || data.tool_trace || [],
        applied: false,
        error: !data.success
    });

    const sendAIMessage = async () => {
        const text = aiInput.value.trim();
        if (!text || aiLoading.value) return;

        aiMessages.value.push({ role: 'user', content: text });
        aiInput.value = '';
        aiLoading.value = true;
        aiError.value = '';
        startLoadingProgress();
        scrollAIMessages();

        try {
            const payloadMessages = aiMessages.value
                .filter(m => m.role === 'user' || m.role === 'assistant')
                .slice(-10)
                .map(m => ({ role: m.role, content: m.content }));
            const context = {
                deck: getDeck(),
                referenced_tabs: referencedTabPayloads(),
                referenced_cards: referencedCardPayloads(),
                workspace_item_id: typeof options.getWorkspaceItemId === 'function' ? options.getWorkspaceItemId() : null,
                language: typeof options.getLanguage === 'function' ? options.getLanguage() : 'tw',
                standard_marks: ['H', 'I', 'J'],
                deep_think: aiDeepThink.value,
                max_agent_steps: aiDeepThink.value ? 12 : 6
            };
            const res = await fetch('/api/ai/chat/jobs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ messages: payloadMessages, context })
            });
            const job = await res.json();
            if (!job.success || !job.job_id) throw new Error(job.error || 'AI job failed to start');
            const data = await pollAIJob(job.job_id);
            aiToolSteps.value = data.steps || data.tool_trace || [];
            aiLastCards.value = data.cards || [];
            aiMessages.value.push(normalizeMessage(data));
        } catch (e) {
            aiError.value = e && e.message ? e.message : 'AI 連線失敗';
            aiMessages.value.push({ role: 'assistant', content: aiError.value, error: true });
        } finally {
            stopPolling();
            stopLoadingProgress();
            aiLoading.value = false;
            scrollAIMessages();
        }
    };

    const pollAIJob = (jobId) => new Promise((resolve, reject) => {
        const startedAt = Date.now();
        const poll = async () => {
            try {
                const res = await fetch(`/api/ai/chat/jobs/${encodeURIComponent(jobId)}`);
                const data = await res.json();
                if (!data.success) throw new Error(data.error || 'AI job status failed');

                if (data.steps && data.steps.length) {
                    stopLoadingProgress();
                    aiToolSteps.value = data.steps;
                    aiLoadingMessage.value = data.message || data.steps[data.steps.length - 1].message || 'Agent 正在工作';
                    scrollAIMessages();
                }

                if (data.status === 'finished') {
                    resolve(data.result || {});
                    return;
                }
                if (data.status === 'failed') {
                    reject(new Error(data.error || data.message || 'AI job failed'));
                    return;
                }
                if (Date.now() - startedAt > 240000) {
                    reject(new Error('AI job timeout'));
                    return;
                }
                aiPollTimer = window.setTimeout(poll, 1000);
            } catch (e) {
                reject(e);
            }
        };
        poll();
    });

    const clearAIChat = () => {
        aiMessages.value = [
            {
                role: 'assistant',
                content: '已清空對話。你可以問我標準卡牌效果、Meta 構築，或要求我產生可套用的牌組修改。'
            }
        ];
        aiLastCards.value = [];
        aiToolSteps.value = [];
        aiError.value = '';
        stopPolling();
        stopLoadingProgress();
        scrollAIMessages();
    };

    const openAICard = (card) => {
        if (typeof openCardModal === 'function') {
            openCardModal(card, 'search');
        }
    };

    const actionLabel = (action) => {
        if (!action) return '';
        if (action.type === 'remove_card' || action.type === 'remove') return `移除 ${action.count || 0} 張 ${action.card_name || action.name || ''}`;
        if (action.type === 'add_card' || action.type === 'add') return `加入 ${action.count || 0} 張 ${action.card_name || action.name || ''}`;
        return `${action.type || 'action'} ${action.card_name || action.name || ''}`;
    };

    const metaTitle = (ref) => {
        if (!ref) return '';
        if (ref.type === 'archetype') return ref.archetype || ref.title || 'Meta archetype';
        return [ref.archetype || ref.title || ref.deck_id, ref.tournament_title, ref.date].filter(Boolean).join(' · ');
    };

    const decklistTitle = (decklist) => {
        if (!decklist) return '推薦牌表';
        return decklist.name || decklist.archetype || decklist.deck_id || '推薦牌表';
    };

    const decklistSubtitle = (decklist) => {
        const meta = decklist && decklist.meta ? decklist.meta : {};
        return [
            meta.player_name ? `玩家 ${meta.player_name}` : '',
            meta.placement ? `#${meta.placement}` : '',
            meta.tournament_title || '',
            meta.date || '',
        ].filter(Boolean).join(' · ');
    };

    const decklistTotal = (decklist) => (decklist && decklist.cards ? decklist.cards : [])
        .reduce((sum, card) => sum + parseInt(card.count || 0), 0);

    const decklistSections = (decklist) => {
        const cards = (decklist && decklist.cards) ? decklist.cards : [];
        const definitions = [
            { key: 'pokemon', label: 'Pokémon' },
            { key: 'trainer', label: 'Trainer' },
            { key: 'energy', label: 'Energy' },
            { key: 'unknown', label: 'Other' },
        ];
        return definitions.map(section => {
            const sectionCards = cards.filter(card => (card.section || 'unknown') === section.key);
            return {
                ...section,
                cards: sectionCards,
                total: sectionCards.reduce((sum, card) => sum + parseInt(card.count || 0), 0)
            };
        }).filter(section => section.cards.length);
    };

    const cardDisplayName = (card) => card ? (card.name || card.card_name || card.jp_card_name || '') : '';

    const decklistCardKey = (decklist, card, index) => [
        decklist && decklist.deck_id,
        card && (card.card_id || card.id || card.name || card.card_name),
        card && card.section,
        index
    ].filter(Boolean).join('-');

    const expandDecklistCards = (decklist) => {
        const cards = (decklist && decklist.cards) ? decklist.cards : [];
        const expanded = [];
        for (const card of cards) {
            const count = parseInt(card && card.count || 0);
            if (!card || !count) continue;
            if (!card.card_id || card.missing) continue;
            const base = { ...card };
            delete base.count;
            for (let i = 0; i < count; i++) expanded.push({ ...base });
        }
        return expanded;
    };

    const unresolvedDecklistCards = (decklist) => (decklist && decklist.cards ? decklist.cards : [])
        .filter(card => card && card.count && (!card.card_id || card.missing))
        .map(card => ({
            ...card,
            name: cardDisplayName(card),
            count: parseInt(card.count || 0),
            image_url: card.image_url || card.image || '',
            reason: card.reason || card.match_error || 'not matched'
        }));

    const buildAIDeckImportPayload = (decklist, expanded, title, extraMeta = {}) => ({
        source: 'ai',
        title,
        cards: expanded,
        meta: {
            source: 'ai',
            deck_id: decklist && decklist.deck_id ? decklist.deck_id : null,
            ...(decklist && decklist.meta ? decklist.meta : {}),
            ...extraMeta
        }
    });

    const openAIDeckImportPayload = (payload) => {
        if (!payload || !Array.isArray(payload.cards) || !payload.cards.length) return false;
        if (typeof options.openImportedDeckTab === 'function') {
            options.openImportedDeckTab(payload);
            return true;
        }
        if (typeof options.replaceDeck === 'function') {
            options.replaceDeck(payload.cards, payload.title);
            return true;
        }

        const deck = getDeck();
        if (Array.isArray(deck)) {
            deck.splice(0, deck.length, ...payload.cards.map(card => ({
                ...card,
                uniqueId: Date.now() + Math.random().toString(36).substr(2, 9)
            })));
            return true;
        }
        return false;
    };

    const importAIDecklistNew = (decklist) => {
        const expanded = expandDecklistCards(decklist);
        const total = decklistTotal(decklist);
        const title = decklistTitle(decklist);
        const missing = unresolvedDecklistCards(decklist);

        if (missing.length || !expanded.length || expanded.length !== total) {
            pendingAIDeckImport.value = {
                decklist,
                title,
                total,
                resolved: expanded.length,
                expanded,
                missing,
                reason: missing.length
                    ? 'missing'
                    : (!expanded.length ? 'empty' : 'count_mismatch')
            };
            return;
        }

        openAIDeckImportPayload(buildAIDeckImportPayload(decklist, expanded, title));
    };

    const cancelAIDeckImport = () => {
        pendingAIDeckImport.value = null;
    };

    const confirmAIDeckPartialImport = () => {
        const pending = pendingAIDeckImport.value;
        if (!pending || !pending.expanded || !pending.expanded.length) {
            pendingAIDeckImport.value = null;
            return;
        }
        const payload = buildAIDeckImportPayload(
            pending.decklist,
            pending.expanded,
            pending.title,
            {
                partial_import: true,
                original_total: pending.total,
                resolved_total: pending.resolved,
                missing_cards: pending.missing
            }
        );
        openAIDeckImportPayload(payload);
        pendingAIDeckImport.value = null;
    };

    const addAIDecklistCard = (card, count = 1) => {
        if (!card || !card.card_id || card.missing) {
            alert('這張牌尚未對應到本地卡池，暫不能直接加入。');
            return;
        }
        const run = () => {
            for (let i = 0; i < count; i++) {
                if (typeof options.addToDeck === 'function') options.addToDeck(card);
            }
        };
        if (typeof options.withTimelineBatch === 'function') {
            options.withTimelineBatch('AI 加入牌表卡片', run);
        } else {
            run();
            if (typeof options.markTimelineAction === 'function') options.markTimelineAction('AI 加入牌表卡片');
        }
    };

    const applyAIMessageActions = (message) => {
        if (!message || message.applied || !message.deck_actions || !message.deck_actions.length) return;
        const diff = message.deck_diff || {};
        const warnings = (diff.warnings || []).join('\n');
        const lines = [
            `目前張數：${diff.current_total ?? getDeck().length}`,
            `套用後張數：${diff.projected_total ?? getDeck().length}`,
            ...(diff.removals || []).map(item => `- 移除 ${item.count} 張 ${item.card_name}`),
            ...(diff.additions || []).map(item => `+ 加入 ${item.count} 張 ${item.card_name}`),
            warnings ? `\n注意：\n${warnings}` : ''
        ].filter(Boolean);
        if (!confirm(`套用 AI 建議變更？\n\n${lines.join('\n')}`)) return;

        const unresolved = message.deck_actions
            .filter(action => action.type === 'add_card' || action.type === 'add')
            .filter(action => {
                const name = action.card_name || action.name;
                return !action.card && !(message.cards || []).find(c => c.name === name);
            });
        if (unresolved.length) {
            alert(`以下卡片尚未解析，暫不能套用：\n${unresolved.map(action => action.card_name || action.name).join('\n')}`);
            return;
        }

        const run = () => {
            const deck = getDeck();
            for (const action of message.deck_actions) {
                const type = action.type;
                const name = action.card_name || action.name;
                const count = parseInt(action.count || 0);
                if (!name || !count) continue;
                if (type === 'remove_card' || type === 'remove') {
                    for (let i = 0; i < count; i++) {
                        const found = deck.find(c => c.name === name);
                        if (!found) break;
                        if (typeof options.removeCardInstance === 'function') {
                            options.removeCardInstance(found.uniqueId);
                        }
                    }
                } else if (type === 'add_card' || type === 'add') {
                    const card = action.card || (message.cards || []).find(c => c.name === name);
                    if (!card) continue;
                    for (let i = 0; i < count; i++) {
                        if (typeof options.addToDeck === 'function') options.addToDeck(card);
                    }
                }
            }
        };

        if (typeof options.withTimelineBatch === 'function') {
            options.withTimelineBatch('AI 套用建議', run);
        } else {
            run();
            if (typeof options.markTimelineAction === 'function') options.markTimelineAction('AI 套用建議');
        }
        message.applied = true;
        if (!options.hasWorkspaceItem || !options.hasWorkspaceItem()) {
            alert('已套用到目前牌組。此牌組尚未連到 workspace item，請記得保存。');
        }
    };

    return {
        aiPanelOpen,
        aiMessages,
        aiInput,
        aiLoading,
        aiError,
        aiLastCards,
        aiToolSteps,
        aiDeepThink,
        aiLoadingMessage,
        aiReferencedTabs,
        aiReferencedCards,
        aiTabPickerOpen,
        aiDropActive,
        availableAITabs,
        pendingAIDeckImport,
        openAIPanel,
        closeAIPanel,
        toggleAIPanel,
        sendAIMessage,
        clearAIChat,
        openAICard,
        addAITabReference,
        removeAITabReference,
        addAICardReference,
        removeAICardReference,
        handleAIDragOver,
        handleAIDragLeave,
        handleAIDrop,
        renderMarkdown,
        actionLabel,
        metaTitle,
        decklistTitle,
        decklistSubtitle,
        decklistTotal,
        decklistSections,
        cardDisplayName,
        decklistCardKey,
        importAIDecklist: importAIDecklistNew,
        cancelAIDeckImport,
        confirmAIDeckPartialImport,
        addAIDecklistCard,
        applyAIMessageActions
    };
}
