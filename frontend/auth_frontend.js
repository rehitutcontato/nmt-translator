/**
 * NMT — Fase 2
 * auth_frontend.js
 *
 * Módulo de autenticação para o frontend.
 * Cole este bloco no início do script.js existente,
 * ANTES da função connectWS().
 *
 * Responsabilidades:
 *   - Armazenar e recuperar tokens do localStorage
 *   - Verificar se o usuário está logado ao carregar a página
 *   - Mostrar modal de login/cadastro se não autenticado
 *   - Renovar access token automaticamente antes de expirar
 *   - Injetar token no WebSocket URL
 *   - Exibir informações do usuário no header
 */

// ══════════════════════════════════════════════
// STORAGE — tokens em localStorage
// ══════════════════════════════════════════════

const Auth = {
  // ── Armazenamento ──────────────────────────

  setTokens(accessToken, refreshToken, expiresIn) {
    localStorage.setItem('nmt_access_token', accessToken);
    localStorage.setItem('nmt_refresh_token', refreshToken);
    // Salva o timestamp de expiração (com margem de 60s)
    const expiresAt = Date.now() + (expiresIn - 60) * 1000;
    localStorage.setItem('nmt_token_expires_at', expiresAt.toString());
  },

  getAccessToken() {
    return localStorage.getItem('nmt_access_token');
  },

  getRefreshToken() {
    return localStorage.getItem('nmt_refresh_token');
  },

  isTokenExpired() {
    const expiresAt = parseInt(localStorage.getItem('nmt_token_expires_at') || '0');
    return Date.now() >= expiresAt;
  },

  clearTokens() {
    localStorage.removeItem('nmt_access_token');
    localStorage.removeItem('nmt_refresh_token');
    localStorage.removeItem('nmt_token_expires_at');
    localStorage.removeItem('nmt_user');
  },

  saveUser(userData) {
    localStorage.setItem('nmt_user', JSON.stringify(userData));
  },

  getUser() {
    try {
      return JSON.parse(localStorage.getItem('nmt_user') || 'null');
    } catch {
      return null;
    }
  },

  isLoggedIn() {
    return !!this.getAccessToken();
  },

  // ── API calls ──────────────────────────────

  get API_BASE() {
    return window.location.hostname === 'localhost'
      ? 'http://localhost:8000'
      : 'https://nmt.up.railway.app';
  },

  async register(email, password, name) {
    const res = await fetch(`${this.API_BASE}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, name }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Erro ao criar conta');
    this.setTokens(data.access_token, data.refresh_token, data.expires_in);
    return data;
  },

  async login(email, password) {
    const res = await fetch(`${this.API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Email ou senha incorretos');
    this.setTokens(data.access_token, data.refresh_token, data.expires_in);
    return data;
  },

  async refreshAccessToken() {
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) return false;

    try {
      const res = await fetch(`${this.API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) {
        this.clearTokens();
        return false;
      }
      const data = await res.json();
      this.setTokens(data.access_token, data.refresh_token, data.expires_in);
      return true;
    } catch {
      return false;
    }
  },

  async fetchMe() {
    const token = await this.getValidToken();
    if (!token) return null;

    const res = await fetch(`${this.API_BASE}/auth/me`, {
      headers: { 'Authorization': `Bearer ${token}` },
    });
    if (!res.ok) return null;
    const user = await res.json();
    this.saveUser(user);
    return user;
  },

  async logout() {
    const token = this.getAccessToken();
    if (token) {
      fetch(`${this.API_BASE}/auth/logout`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
      }).catch(() => {}); // fire-and-forget
    }
    this.clearTokens();
    showAuthModal('login');
  },

  // ── Token válido (renova se necessário) ────

  async getValidToken() {
    if (!this.isLoggedIn()) return null;
    if (this.isTokenExpired()) {
      const ok = await this.refreshAccessToken();
      if (!ok) return null;
    }
    return this.getAccessToken();
  },

  // ── WebSocket URL com token ─────────────────

  async getWebSocketURL() {
    const token = await this.getValidToken();
    if (!token) return null;
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const host = window.location.hostname === 'localhost'
      ? 'localhost:8000'
      : 'nmt.up.railway.app';
    return `${proto}://${host}/ws/translate?token=${encodeURIComponent(token)}`;
  },
};


// ══════════════════════════════════════════════
// AUTH MODAL
// Injeta o modal no DOM se não existir
// ══════════════════════════════════════════════

function injectAuthModal() {
  if (document.getElementById('auth-modal')) return;

  const modal = document.createElement('div');
  modal.id = 'auth-modal';
  modal.innerHTML = `
    <div class="auth-overlay" id="auth-overlay">
      <div class="auth-card">
        <div class="auth-logo">NMT</div>

        <!-- Tabs -->
        <div class="auth-tabs">
          <button class="auth-tab active" data-tab="login" onclick="switchTab('login')">Entrar</button>
          <button class="auth-tab" data-tab="register" onclick="switchTab('register')">Criar conta</button>
        </div>

        <!-- Login -->
        <div class="auth-form" id="tab-login">
          <div class="auth-field">
            <label>Email</label>
            <input type="email" id="login-email" placeholder="seu@email.com" autocomplete="email">
          </div>
          <div class="auth-field">
            <label>Senha</label>
            <input type="password" id="login-password" placeholder="••••••••" autocomplete="current-password">
          </div>
          <div class="auth-error" id="login-error"></div>
          <button class="auth-btn-primary" onclick="handleLogin()" id="btn-login">Entrar</button>
        </div>

        <!-- Register -->
        <div class="auth-form hidden" id="tab-register">
          <div class="auth-field">
            <label>Nome (opcional)</label>
            <input type="text" id="reg-name" placeholder="Seu nome" autocomplete="name">
          </div>
          <div class="auth-field">
            <label>Email</label>
            <input type="email" id="reg-email" placeholder="seu@email.com" autocomplete="email">
          </div>
          <div class="auth-field">
            <label>Senha</label>
            <input type="password" id="reg-password" placeholder="Mín. 8 chars, 1 número, 1 especial" autocomplete="new-password">
          </div>
          <div class="auth-field">
            <label class="auth-consent">
              <input type="checkbox" id="reg-consent">
              Li e aceito a <a href="/privacy" target="_blank">Política de Privacidade</a>
            </label>
          </div>
          <div class="auth-error" id="reg-error"></div>
          <button class="auth-btn-primary" onclick="handleRegister()" id="btn-register">Criar conta grátis</button>
        </div>

        <div class="auth-free-note">
          Plano Free: 30 minutos/mês grátis. Sem cartão.
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  // Enter key nos inputs
  ['login-email', 'login-password'].forEach(id => {
    document.getElementById(id)?.addEventListener('keydown', e => {
      if (e.key === 'Enter') handleLogin();
    });
  });
  ['reg-name', 'reg-email', 'reg-password'].forEach(id => {
    document.getElementById(id)?.addEventListener('keydown', e => {
      if (e.key === 'Enter') handleRegister();
    });
  });
}

function showAuthModal(tab = 'login') {
  injectAuthModal();
  switchTab(tab);
  document.getElementById('auth-modal').style.display = 'flex';
}

function hideAuthModal() {
  const modal = document.getElementById('auth-modal');
  if (modal) modal.style.display = 'none';
}

function switchTab(tab) {
  document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.auth-form').forEach(f => f.classList.add('hidden'));
  document.querySelector(`[data-tab="${tab}"]`)?.classList.add('active');
  document.getElementById(`tab-${tab}`)?.classList.remove('hidden');
}

function setAuthLoading(btnId, loading) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.disabled = loading;
  btn.textContent = loading
    ? (btnId === 'btn-login' ? 'Entrando...' : 'Criando conta...')
    : (btnId === 'btn-login' ? 'Entrar' : 'Criar conta grátis');
}

async function handleLogin() {
  const email = document.getElementById('login-email')?.value?.trim();
  const password = document.getElementById('login-password')?.value;
  const errorEl = document.getElementById('login-error');

  if (!email || !password) {
    errorEl.textContent = 'Preencha email e senha.';
    return;
  }
  errorEl.textContent = '';
  setAuthLoading('btn-login', true);

  try {
    await Auth.login(email, password);
    await onAuthSuccess();
  } catch (err) {
    errorEl.textContent = err.message;
  } finally {
    setAuthLoading('btn-login', false);
  }
}

async function handleRegister() {
  const name = document.getElementById('reg-name')?.value?.trim();
  const email = document.getElementById('reg-email')?.value?.trim();
  const password = document.getElementById('reg-password')?.value;
  const consent = document.getElementById('reg-consent')?.checked;
  const errorEl = document.getElementById('reg-error');

  if (!email || !password) {
    errorEl.textContent = 'Preencha email e senha.';
    return;
  }
  if (!consent) {
    errorEl.textContent = 'Aceite a Política de Privacidade para continuar.';
    return;
  }
  errorEl.textContent = '';
  setAuthLoading('btn-register', true);

  try {
    await Auth.register(email, password, name || null);
    await onAuthSuccess();
  } catch (err) {
    errorEl.textContent = err.message;
  } finally {
    setAuthLoading('btn-register', false);
  }
}

// ── Pós-autenticação ──────────────────────────

async function onAuthSuccess() {
  const user = await Auth.fetchMe();
  if (user) {
    updateUserHeader(user);
  }
  hideAuthModal();
  // Reconectar WebSocket com token válido
  if (typeof connectWS === 'function') {
    connectWS();
  }
}

// ══════════════════════════════════════════════
// HEADER DO USUÁRIO
// ══════════════════════════════════════════════

function updateUserHeader(user) {
  const headerEl = document.getElementById('user-header');
  if (!headerEl) return;

  const planColors = {
    free: '#8888AA',
    starter: '#00D4FF',
    pro: '#7B2FBE',
    business: '#F59E0B',
    enterprise: '#10B981',
  };
  const planColor = planColors[user.plan_id] || '#8888AA';

  const minutesUsed = user.minutes_used_this_month || 0;
  const minutesLimit = user.minutes_limit;
  const isUnlimited = minutesLimit === -1;
  const pct = isUnlimited ? 0 : Math.min((minutesUsed / minutesLimit) * 100, 100);

  headerEl.innerHTML = `
    <div class="user-info">
      <span class="user-plan" style="color: ${planColor}">${user.plan_name}</span>
      <span class="user-email">${user.name || user.email}</span>
    </div>
    ${!isUnlimited ? `
    <div class="usage-bar-container" title="${minutesUsed.toFixed(1)}/${minutesLimit} min usados">
      <div class="usage-bar">
        <div class="usage-bar-fill" style="width: ${pct}%; background: ${pct > 85 ? '#EF4444' : planColor}"></div>
      </div>
      <span class="usage-label">${minutesUsed.toFixed(0)}/${minutesLimit} min</span>
    </div>` : '<span class="usage-unlimited">∞ ilimitado</span>'}
    <button class="btn-logout" onclick="Auth.logout()">Sair</button>
  `;
  headerEl.style.display = 'flex';
}

// ══════════════════════════════════════════════
// INICIALIZAÇÃO
// Chame esta função no DOMContentLoaded do script.js
// ══════════════════════════════════════════════

async function initAuth() {
  if (!Auth.isLoggedIn()) {
    showAuthModal('login');
    return false;
  }

  // Token pode estar expirado — tenta renovar
  const token = await Auth.getValidToken();
  if (!token) {
    showAuthModal('login');
    return false;
  }

  // Carregar dados do usuário
  const user = await Auth.fetchMe();
  if (user) {
    updateUserHeader(user);
  }

  return true;
}

// ── Renovação automática de token ─────────────
// Verifica a cada 5 minutos se o token precisa ser renovado
setInterval(async () => {
  if (Auth.isLoggedIn() && Auth.isTokenExpired()) {
    const ok = await Auth.refreshAccessToken();
    if (!ok) {
      // Token de refresh também expirou — pede login novamente
      showAuthModal('login');
    }
  }
}, 5 * 60 * 1000);


// ══════════════════════════════════════════════
// CSS DO MODAL (injeta no <head> automaticamente)
// ══════════════════════════════════════════════

(function injectAuthStyles() {
  if (document.getElementById('auth-modal-styles')) return;
  const style = document.createElement('style');
  style.id = 'auth-modal-styles';
  style.textContent = `
    #auth-modal {
      display: none;
      position: fixed; inset: 0; z-index: 9999;
    }
    .auth-overlay {
      display: flex; align-items: center; justify-content: center;
      width: 100%; height: 100%;
      background: rgba(10, 10, 15, 0.92);
      backdrop-filter: blur(12px);
    }
    .auth-card {
      background: #12121A;
      border: 1px solid #1E1E2E;
      border-radius: 16px;
      padding: 36px;
      width: 100%; max-width: 420px;
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
    }
    .auth-logo {
      font-size: 22px; font-weight: 800; letter-spacing: -1px;
      color: #00D4FF; margin-bottom: 24px;
    }
    .auth-tabs {
      display: flex; gap: 4px;
      background: #0A0A0F; border-radius: 8px; padding: 4px;
      margin-bottom: 24px;
    }
    .auth-tab {
      flex: 1; padding: 8px; border: none; border-radius: 6px;
      background: transparent; color: #8888AA;
      font-size: 14px; font-weight: 500; cursor: pointer;
      transition: all 0.15s;
    }
    .auth-tab.active { background: #1E1E2E; color: #FFFFFF; }
    .auth-form.hidden { display: none; }
    .auth-field { margin-bottom: 16px; }
    .auth-field label { display: block; font-size: 13px; color: #8888AA; margin-bottom: 6px; }
    .auth-field input[type="email"],
    .auth-field input[type="password"],
    .auth-field input[type="text"] {
      width: 100%; padding: 10px 14px;
      background: #0A0A0F; border: 1px solid #1E1E2E; border-radius: 8px;
      color: #FFFFFF; font-size: 14px;
      outline: none; box-sizing: border-box;
      transition: border-color 0.15s;
    }
    .auth-field input:focus { border-color: #00D4FF; }
    .auth-consent { display: flex !important; align-items: center; gap: 8px;
                    font-size: 13px !important; cursor: pointer; }
    .auth-consent a { color: #00D4FF; }
    .auth-error { color: #EF4444; font-size: 13px; margin-bottom: 12px; min-height: 18px; }
    .auth-btn-primary {
      width: 100%; padding: 12px;
      background: #00D4FF; color: #0A0A0F;
      border: none; border-radius: 8px;
      font-size: 15px; font-weight: 600; cursor: pointer;
      transition: opacity 0.15s;
    }
    .auth-btn-primary:hover { opacity: 0.9; }
    .auth-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
    .auth-free-note {
      margin-top: 16px; font-size: 12px; color: #555577; text-align: center;
    }

    /* Header do usuário */
    #user-header {
      display: none; align-items: center; gap: 16px;
      padding: 8px 16px;
      background: rgba(18, 18, 26, 0.8);
      border-bottom: 1px solid #1E1E2E;
      position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    }
    .user-info { display: flex; flex-direction: column; gap: 2px; }
    .user-plan { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
    .user-email { font-size: 13px; color: #8888AA; }
    .usage-bar-container { display: flex; align-items: center; gap: 8px; flex: 1; }
    .usage-bar { flex: 1; height: 4px; background: #1E1E2E; border-radius: 2px; overflow: hidden; }
    .usage-bar-fill { height: 100%; border-radius: 2px; transition: width 0.3s; }
    .usage-label { font-size: 11px; color: #555577; white-space: nowrap; }
    .usage-unlimited { font-size: 13px; color: #10B981; flex: 1; }
    .btn-logout {
      padding: 6px 14px; background: transparent;
      border: 1px solid #1E1E2E; border-radius: 6px;
      color: #8888AA; font-size: 12px; cursor: pointer;
      transition: all 0.15s;
    }
    .btn-logout:hover { border-color: #EF4444; color: #EF4444; }
  `;
  document.head.appendChild(style);
})();
