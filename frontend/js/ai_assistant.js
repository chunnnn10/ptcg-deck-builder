function useAIAssistant(openCardModal) {
    const { ref, nextTick } = Vue;

    const aiPanelOpen = ref(false);
    const aiMessages = ref([
        {
            role: 'assistant',
            content: '可以問我卡牌效果、特性、攻擊條件，或用自然語言搜尋中文/日文卡牌。'
        }
    ]);
    const aiInput = ref('');
    const aiLoading = ref(false);
    const aiError = ref('');
    const aiLastCards = ref([]);
    const aiToolSteps = ref([]);

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
    };

    const toggleAIPanel = () => {
        aiPanelOpen.value = !aiPanelOpen.value;
        if (aiPanelOpen.value) scrollAIMessages();
    };

    const sendAIMessage = async () => {
        const text = aiInput.value.trim();
        if (!text || aiLoading.value) return;

        aiMessages.value.push({ role: 'user', content: text });
        aiInput.value = '';
        aiLoading.value = true;
        aiError.value = '';
        aiToolSteps.value = [{ status: 'running', message: 'AI 正在規劃搜尋流程' }];
        scrollAIMessages();

        try {
            const payloadMessages = aiMessages.value
                .filter(m => m.role === 'user' || m.role === 'assistant')
                .slice(-10)
                .map(m => ({ role: m.role, content: m.content }));
            const res = await fetch('/api/ai/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ messages: payloadMessages, context: {} })
            });
            const data = await res.json();
            aiToolSteps.value = data.steps || [];
            if (!data.success) {
                aiError.value = data.error || 'AI response failed';
                aiMessages.value.push({ role: 'assistant', content: aiError.value, error: true, cards: data.cards || [], steps: data.steps || [] });
            } else {
                aiLastCards.value = data.cards || [];
                aiMessages.value.push({ role: 'assistant', content: data.answer || '', cards: data.cards || [], steps: data.steps || [] });
            }
        } catch (e) {
            aiError.value = '無法連接 AI 服務';
            aiMessages.value.push({ role: 'assistant', content: aiError.value, error: true });
        } finally {
            aiLoading.value = false;
            scrollAIMessages();
        }
    };

    const clearAIChat = () => {
        aiMessages.value = [
            {
                role: 'assistant',
                content: '對話已清除。可以問：皮卡丘是什麼卡？或 有哪些特性能夠抽牌？'
            }
        ];
        aiLastCards.value = [];
        aiToolSteps.value = [];
        aiError.value = '';
        scrollAIMessages();
    };

    const openAICard = (card) => {
        if (typeof openCardModal === 'function') {
            openCardModal(card, 'search');
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
        openAIPanel,
        closeAIPanel,
        toggleAIPanel,
        sendAIMessage,
        clearAIChat,
        openAICard,
        renderMarkdown
    };
}
