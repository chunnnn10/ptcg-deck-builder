// /Pokemon/public/js/auth.js

function useAuth() {
    const { ref, reactive, onMounted } = Vue;

    const user = ref(null);
    const isAuthenticated = ref(false);
    const showAuthModal = ref(false);
    const showUserMenu = ref(false); // [新增] 用戶選單開關
    const authMode = ref('login'); // 'login' 或 'register'
    const authForm = reactive({ username: '', email: '', password: '' });
    const isLoading = ref(false);

    // === 忘記密碼狀態 ===
    const showForgotModal = ref(false);
    const forgotStep = ref('email'); // 'email' | 'reset'
    const forgotEmail = ref('');
    const forgotToken = ref('');
    const forgotPassword = ref('');
    const forgotPassword2 = ref('');
    const forgotMessage = ref('');
    const forgotLoading = ref(false);
    const forgotVerifyLink = ref('');

    const checkAuth = async () => {
        try {
            const res = await fetch('/api/auth/user');
            const data = await res.json();
            if (data.is_authenticated) {
                user.value = data.user;
                isAuthenticated.value = true;
            } else {
                user.value = null;
                isAuthenticated.value = false;
            }
        } catch (e) {
            console.error("Auth check failed", e);
        }
    };

    const login = async () => {
        if (!authForm.username || !authForm.password) return alert("請輸入帳號/Email與密碼");
        isLoading.value = true;
        try {
            const res = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: authForm.username, password: authForm.password })
            });
            const data = await res.json();
            if (data.success) {
                user.value = data.user;
                isAuthenticated.value = true;
                showAuthModal.value = false;
                authForm.password = '';
            } else {
                alert(data.error || "登入失敗");
            }
        } catch (e) {
            alert("連線錯誤");
        } finally {
            isLoading.value = false;
        }
    };

    const register = async () => {
        if (!authForm.username || !authForm.email || !authForm.password) return alert("請填寫所有欄位");
        isLoading.value = true;
        try {
            const res = await fetch('/api/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(authForm)
            });
            const data = await res.json();
            if (data.success) {
                let msg = data.message;
                // 如果有驗證連結 (開發模式)，顯示它
                if (data.verify_link) {
                    msg += "\n\n驗證連結：\n" + data.verify_link;
                }
                alert(msg);
                showAuthModal.value = false;
                authMode.value = 'login';
                authForm.password = '';
            } else {
                alert(data.error || "註冊失敗");
            }
        } catch (e) {
            alert("連線錯誤");
        } finally {
            isLoading.value = false;
        }
    };

    const logout = async () => {
        if (!confirm("確定要登出嗎？")) return;
        try {
            await fetch('/api/auth/logout', { method: 'POST' });
            user.value = null;
            isAuthenticated.value = false;
            showUserMenu.value = false; // 關閉選單
            window.location.reload(); 
        } catch (e) {
            console.error(e);
        }
    };

    const openAuthModal = (mode = 'login') => {
        authMode.value = mode;
        authForm.username = '';
        authForm.email = '';
        authForm.password = '';
        showAuthModal.value = true;
    };

    // === 忘記密碼方法 ===
    const openForgotModal = () => {
        showAuthModal.value = false;
        showForgotModal.value = true;
        forgotStep.value = 'email';
        forgotEmail.value = '';
        forgotToken.value = '';
        forgotPassword.value = '';
        forgotPassword2.value = '';
        forgotMessage.value = '';
        forgotVerifyLink.value = '';
    };

    const closeForgotModal = () => {
        showForgotModal.value = false;
    };

    const submitForgotEmail = async () => {
        if (!forgotEmail.value.trim()) {
            forgotMessage.value = '請輸入 Email';
            return;
        }
        forgotLoading.value = true;
        forgotMessage.value = '';
        forgotVerifyLink.value = '';
        try {
            const res = await fetch('/api/auth/forgot-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: forgotEmail.value.trim() })
            });
            const data = await res.json();
            if (data.success) {
                forgotMessage.value = data.message;
                if (data.verify_link) {
                    forgotVerifyLink.value = data.verify_link;
                }
            } else {
                forgotMessage.value = data.error || '發送失敗';
            }
        } catch (e) {
            forgotMessage.value = '連線錯誤';
        } finally {
            forgotLoading.value = false;
        }
    };

    const submitResetPassword = async () => {
        if (!forgotPassword.value) {
            forgotMessage.value = '請輸入新密碼';
            return;
        }
        if (forgotPassword.value.length < 6) {
            forgotMessage.value = '密碼長度至少需要 6 個字元';
            return;
        }
        if (forgotPassword.value !== forgotPassword2.value) {
            forgotMessage.value = '兩次輸入的密碼不一致';
            return;
        }
        forgotLoading.value = true;
        forgotMessage.value = '';
        try {
            const res = await fetch('/api/auth/reset-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    token: forgotToken.value,
                    password: forgotPassword.value
                })
            });
            const data = await res.json();
            if (data.success) {
                forgotMessage.value = data.message;
                // 3 秒後關閉 modal
                setTimeout(() => {
                    showForgotModal.value = false;
                    openAuthModal('login');
                }, 3000);
            } else {
                forgotMessage.value = data.error || '重設失敗';
            }
        } catch (e) {
            forgotMessage.value = '連線錯誤';
        } finally {
            forgotLoading.value = false;
        }
    };

    // 檢查 URL 是否帶有 reset token (從郵件連結進入)
    const checkResetToken = () => {
        const path = window.location.pathname;
        const match = path.match(/^\/reset-password\/(.+)$/);
        if (match) {
            forgotToken.value = match[1];
            showForgotModal.value = true;
            forgotStep.value = 'reset';
        }
    };

    onMounted(() => {
        checkAuth();
        checkResetToken();
    });

    return {
        user, isAuthenticated, showAuthModal, showUserMenu, authMode, authForm, isLoading,
        checkAuth, login, register, logout, openAuthModal,
        // 忘記密碼
        showForgotModal, forgotStep, forgotEmail, forgotToken,
        forgotPassword, forgotPassword2, forgotMessage, forgotLoading, forgotVerifyLink,
        openForgotModal, closeForgotModal, submitForgotEmail, submitResetPassword
    };
}