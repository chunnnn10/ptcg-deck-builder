// /Pokemon/public/js/simulation.js

function useSimulation(deck, isStandardMode) {
    const { ref, reactive, computed } = Vue;

    const showSimModal = ref(false);
    const isSimulating = ref(false);
    const simProgress = ref(0);
    const simTab = ref('hand'); 
    const simMobileView = ref('settings'); // 'settings' | 'results' [New]

    // [新增] 數學計算用的相關狀態
    const mathTargets = reactive(new Set()); // 用戶勾選要計算的卡片
    
    const simSettings = reactive({
        iterations: 100,
        initialState: 'sorted',
        methods: { hindu: true, mash: true },
        shuffleCount: 5
    });

    const simResults = reactive({
        lastHand: [],
        lastPrizes: [],
        isMulligan: false,
        stats: { totalRuns: 0, mulliganCount: 0, mulliganRate: 0, cardStats: {} },
        prizeTargets: new Set(),
        prizeStats: []
    });

    const isBasicPokemon = (card) => {
        if (!card) return false;
        if (card.sub_type && (card.sub_type.includes('Basic') || card.sub_type.includes('基礎'))) return true;
        const name = card.name || "";
        if (name.includes('VMAX') || name.includes('VSTAR') || name.includes('V-UNION') || name.includes('進化')) return false;
        if (name.includes(' V') || name.includes('ex') || name.includes('輝耀')) return true;
        return false;
    };

    const sortedCardStats = computed(() => {
        const arr = [];
        const total = simSettings.iterations || 1;
        Object.entries(simResults.stats.cardStats).forEach(([name, data]) => {
            arr.push({
                name: name,
                handProb: ((data.handCount / total) * 100).toFixed(1),
                prizeProb: ((data.prizeCount / total) * 100).toFixed(1)
            });
        });
        return arr.sort((a, b) => parseFloat(b.handProb) - parseFloat(a.handProb));
    });

    const getProbColor = (prob) => {
        const p = parseFloat(prob);
        if (p >= 60) return 'text-green-400 font-bold';
        if (p <= 10 && p > 0) return 'text-blue-300';
        return 'text-gray-300';
    };

    const uniqueDeckList = computed(() => {
        const map = new Map();
        deck.value.forEach(c => map.set(c.name, c));
        return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
    });

    const togglePrizeTarget = (name) => {
        if (simResults.prizeTargets.has(name)) simResults.prizeTargets.delete(name);
        else simResults.prizeTargets.add(name);
    };

    const openSimModal = () => {
        if (deck.value.length === 0) return alert("牌組是空的，無法進行模擬！");
        if (isStandardMode.value && deck.value.length !== 60) {
            if (!confirm(`目前牌組只有 ${deck.value.length} 張，標準規則需要 60 張。\n確定要進行模擬嗎？`)) return;
        }
        showSimModal.value = true;
    };

    // === 模擬核心算法 ===
    const shuffleDeck = (originalDeck) => {
        let currentDeck = [...originalDeck];
        // 1. 初始狀態
        if (simSettings.initialState === 'random') {
            for (let i = currentDeck.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1));
                [currentDeck[i], currentDeck[j]] = [currentDeck[j], currentDeck[i]];
            }
        } else {
            currentDeck.sort((a, b) => {
                const getType = (c) => {
                    const t = (c.card_type || '').toLowerCase();
                    if (t.includes('pokemon')) return 1;
                    if (t.includes('energy')) return 3;
                    return 2; 
                };
                return getType(a) - getType(b);
            });
        }
        // 2. 洗牌
        const count = simSettings.shuffleCount;
        const methods = [];
        if (simSettings.methods.hindu) methods.push('hindu');
        if (simSettings.methods.mash) methods.push('mash');
        if (methods.length === 0) return currentDeck;

        for (let i = 0; i < count; i++) {
            const method = methods[i % methods.length];
            if (method === 'hindu') {
                const center = Math.floor(currentDeck.length / 2) + (Math.floor(Math.random() * 11) - 5);
                const grabSize = Math.floor(Math.random() * 21) + 20; 
                let start = Math.max(0, center - Math.floor(grabSize / 2));
                if (start + grabSize > currentDeck.length) start = currentDeck.length - grabSize;
                const chunk = currentDeck.splice(start, grabSize);
                currentDeck.unshift(...chunk);
            } else if (method === 'mash') {
                const splitPoint = (Math.floor(Math.random() * 11) + 25);
                const left = currentDeck.slice(0, splitPoint);
                const right = currentDeck.slice(splitPoint);
                const newDeck = [];
                while (left.length > 0 || right.length > 0) {
                    if (left.length > 0) {
                        const dropCount = Math.random() < 0.3 ? 2 : 1;
                        for(let k=0; k<dropCount && left.length > 0; k++) newDeck.push(left.pop());
                    }
                    if (right.length > 0) {
                        const dropCount = Math.random() < 0.3 ? 2 : 1;
                        for(let k=0; k<dropCount && right.length > 0; k++) newDeck.push(right.pop());
                    }
                }
                currentDeck = newDeck;
            }
        }
        return currentDeck;
    };

    const runSimulation = async () => {
        isSimulating.value = true;
        simProgress.value = 0;
        simResults.stats.totalRuns = 0;
        simResults.stats.mulliganCount = 0;
        simResults.stats.cardStats = {};
        simResults.prizeStats = [];
        
        deck.value.forEach(c => {
            if (!simResults.stats.cardStats[c.name]) simResults.stats.cardStats[c.name] = { handCount: 0, prizeCount: 0 };
        });

        const prizeComboMap = {}; 
        const iterations = simSettings.iterations;
        const batchSize = 500; 
        let currentIter = 0;

        const processBatch = () => {
            const end = Math.min(currentIter + batchSize, iterations);
            for (; currentIter < end; currentIter++) {
                const shuffled = shuffleDeck(deck.value);
                const hand = shuffled.slice(0, 7);
                const prizes = shuffled.slice(7, 13);
                
                const hasBasic = hand.some(c => isBasicPokemon(c));
                if (!hasBasic) simResults.stats.mulliganCount++;

                const handNames = new Set(hand.map(c => c.name));
                const prizeNames = new Set(prizes.map(c => c.name));
                
                handNames.forEach(name => { if (simResults.stats.cardStats[name]) simResults.stats.cardStats[name].handCount++; });
                prizeNames.forEach(name => { if (simResults.stats.cardStats[name]) simResults.stats.cardStats[name].prizeCount++; });

                if (simResults.prizeTargets.size > 0) {
                    const hitTargets = [];
                    prizes.forEach(c => { if (simResults.prizeTargets.has(c.name)) hitTargets.push(c.name); });
                    if (hitTargets.length > 0) {
                        hitTargets.sort();
                        const comboCount = {};
                        hitTargets.forEach(n => comboCount[n] = (comboCount[n] || 0) + 1);
                        const comboKey = Object.entries(comboCount).sort().map(([k, v]) => `${k}:${v}`).join('|');
                        prizeComboMap[comboKey] = (prizeComboMap[comboKey] || 0) + 1;
                    }
                }

                if (currentIter === iterations - 1) {
                    simResults.lastHand = hand;
                    simResults.lastPrizes = prizes;
                    simResults.isMulligan = !hasBasic;
                }
            }
            simProgress.value = Math.floor((currentIter / iterations) * 100);

            if (currentIter < iterations) {
                setTimeout(processBatch, 0);
            } else {
                finishSimulation(prizeComboMap);
            }
        };
        setTimeout(processBatch, 10);
    };

    // [新增] 組合公式 C(n, k)
    const combinations = (n, k) => {
        if (k < 0 || k > n) return 0;
        if (k === 0 || k === n) return 1;
        if (k > n / 2) k = n - k;
        let res = 1;
        for (let i = 1; i <= k; i++) {
            res = res * (n - i + 1) / i;
        }
        return res;
    };

    // [新增] 超幾何分佈計算：總數N, 樣本n, 目標總數K, 抽中k張
    // 回傳抽中 "至少 k 張" 的機率
    const hyperGeoAtLeast = (N, n, K, k) => {
        let prob = 0;
        // 計算抽中 k, k+1, ..., min(n, K) 的機率總和
        for (let i = k; i <= Math.min(n, K); i++) {
            const ways = combinations(K, i) * combinations(N - K, n - i);
            const totalWays = combinations(N, n);
            prob += ways / totalWays;
        }
        return (prob * 100).toFixed(2);
    };

    // [新增] 計算結果列表
    const mathCalcResults = computed(() => {
        const results = [];
        const deckSize = isStandardMode.value ? 60 : deck.value.length;
        const handSize = 7;
        const prizeSize = 6;

        // 統計牌組內每種卡的數量
        const cardCounts = {};
        deck.value.forEach(c => {
            cardCounts[c.name] = (cardCounts[c.name] || 0) + 1;
        });

        // 只計算用戶勾選的卡片，如果沒勾選則計算全部
        const targets = mathTargets.size > 0 ? Array.from(mathTargets) : Object.keys(cardCounts);
        targets.sort(); // 排序

        targets.forEach(name => {
            const count = cardCounts[name] || 0;
            if (count === 0) return;

            results.push({
                name: name,
                count: count,
                // 起手至少有 1 張的機率 (樣本 7)
                handProb: hyperGeoAtLeast(deckSize, handSize, count, 1),
                // 獎賞卡至少有 1 張的機率 (樣本 6)
                prizeProb: hyperGeoAtLeast(deckSize, prizeSize, count, 1),
                // 獎賞卡至少有 2 張的機率 (樣本 6)，只有當投入數 >=2 才計算
                prizeProb2: count >= 2 ? hyperGeoAtLeast(deckSize, prizeSize, count, 2) : 0
            });
        });

        return results;
    });

    const toggleMathTarget = (name) => {
        if (mathTargets.has(name)) mathTargets.delete(name);
        else mathTargets.add(name);
    };

    const finishSimulation = (prizeComboMap) => {
        simResults.stats.totalRuns = simSettings.iterations;
        simResults.stats.mulliganRate = ((simResults.stats.mulliganCount / simSettings.iterations) * 100).toFixed(1);
        
        const processedPrizeStats = [];
        Object.entries(prizeComboMap).forEach(([key, count]) => {
            const comboObj = {};
            key.split('|').forEach(part => {
                const [name, qty] = part.split(':');
                comboObj[name] = parseInt(qty);
            });
            processedPrizeStats.push({
                combo: comboObj,
                prob: ((count / simSettings.iterations) * 100).toFixed(2),
                count: count
            });
        });
        processedPrizeStats.sort((a, b) => parseFloat(b.prob) - parseFloat(a.prob));
        simResults.prizeStats = processedPrizeStats;
        isSimulating.value = false;
        simMobileView.value = 'results'; // Auto switch to results on mobile
    };

    return {
        showSimModal, isSimulating, simProgress, simTab, simSettings, simResults, simMobileView,
        openSimModal, runSimulation, isBasicPokemon, sortedCardStats, getProbColor, uniqueDeckList, togglePrizeTarget,
        mathTargets, toggleMathTarget, mathCalcResults
    };
}