/**
 * xcoin.js — Xcoin 포인트 시스템 클라이언트 SDK
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 *
 * 두 가지를 담고 있습니다.
 *
 *   XcoinBackend  : 게임 백엔드(Node.js)에서 씁니다. 게임 secret 을 들고 서명합니다.
 *   XcoinClient   : 게임 앱/웹에서 씁니다. secret 없이 토큰만 들고 동작합니다.
 *
 * ★ 중요 ★
 * XcoinBackend 는 절대 브라우저나 앱 번들에 넣지 마세요.
 * 게임 secret 이 노출되면 누구나 포인트를 무한히 찍을 수 있습니다.
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 */

/* ============================================================
 * 1) 게임 백엔드용 (Node.js)
 * ============================================================ */

class XcoinBackend {
  /**
   * @param {object} opts
   * @param {string} opts.serverUrl  Xcoin 서버 주소 (예: https://xcoin.example.com)
   * @param {string} opts.gameId     관리자 API 로 등록한 게임 ID
   * @param {string} opts.secret     그 게임의 secret (환경변수에서 읽으세요)
   */
  constructor({ serverUrl, gameId, secret }) {
    if (!serverUrl || !gameId || !secret) {
      throw new Error('serverUrl, gameId, secret 이 모두 필요합니다.');
    }
    this.serverUrl = serverUrl.replace(/\/$/, '');
    this.gameId = gameId;
    this.secret = secret;
  }

  _hmac(payload) {
    // Node.js 전용
    const crypto = require('crypto');
    return crypto.createHmac('sha256', this.secret).update(payload).digest('hex');
  }

  _nonce() {
    const crypto = require('crypto');
    return crypto.randomBytes(16).toString('hex');
  }

  async _post(path, body) {
    const res = await fetch(this.serverUrl + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const json = await res.json();
    if (!res.ok || json.ok === false) {
      const err = new Error(json.message || `요청 실패: ${res.status}`);
      err.code = json.error;
      throw err;
    }
    return json;
  }

  /**
   * 유저가 게임에 로그인했을 때 호출합니다.
   * 받은 user_token 을 앱에 내려주면, 앱은 그 토큰으로 잔액 조회/전환을 할 수 있습니다.
   */
  async issueUserToken(userId) {
    const timestamp = Math.floor(Date.now() / 1000);
    const payload = `auth|${this.gameId}|${userId}|${timestamp}`;
    return this._post('/auth/session', {
      game_id: this.gameId,
      user_id: userId,
      timestamp,
      signature: this._hmac(payload),
    });
  }

  /**
   * 게임 한 판이 끝났을 때 호출합니다.
   *
   * @param {string} userId
   * @param {number} score       그 판의 점수
   * @param {number} durationMs  그 판을 플레이한 시간(밀리초)
   * @param {string} [nonce]     이 판의 고유 ID. 안 주면 자동 생성.
   *                             재시도할 땐 같은 값을 다시 주면 중복 지급되지 않습니다.
   */
  async submitScore(userId, score, durationMs, nonce) {
    const timestamp = Math.floor(Date.now() / 1000);
    const n = nonce || this._nonce();
    const payload = `${this.gameId}|${userId}|${Math.trunc(score)}|${Math.trunc(durationMs)}|${n}|${timestamp}`;
    return this._post('/points/submit', {
      game_id: this.gameId,
      user_id: userId,
      score: Math.trunc(score),
      duration_ms: Math.trunc(durationMs),
      nonce: n,
      timestamp,
      signature: this._hmac(payload),
    });
  }
}

/* ============================================================
 * 2) 게임 앱/웹용
 * ============================================================ */

class XcoinClient {
  /**
   * @param {object} opts
   * @param {string} opts.serverUrl
   * @param {string} opts.userId
   * @param {string} opts.userToken  백엔드에서 받은 토큰
   */
  constructor({ serverUrl, userId, userToken }) {
    this.serverUrl = (serverUrl || '').replace(/\/$/, '');
    this.userId = userId;
    this.userToken = userToken || '';
  }

  setUserToken(token) {
    this.userToken = token;
  }

  async _req(method, path, body) {
    const headers = { 'Content-Type': 'application/json' };
    if (this.userToken) headers['X-User-Token'] = this.userToken;
    const res = await fetch(this.serverUrl + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok || json.ok === false) {
      const err = new Error(json.message || `요청 실패: ${res.status}`);
      err.code = json.error;
      throw err;
    }
    return json;
  }

  _q(params) {
    return '?' + new URLSearchParams(params).toString();
  }

  /** 서버 정책(전환 비율, 최소 전환량 등) */
  getConfig() {
    return this._req('GET', '/config');
  }

  /** 내 포인트 잔액 */
  getBalance() {
    return this._req('GET', '/points/balance' + this._q({ user_id: this.userId }));
  }

  /** 포인트 적립/사용 내역 */
  getHistory(limit = 50) {
    return this._req('GET', '/points/history' + this._q({ user_id: this.userId, limit }));
  }

  /** 연동된 지갑 정보 */
  getWallet() {
    return this._req('GET', '/wallet' + this._q({ user_id: this.userId }));
  }

  /** 전환 견적: 이 포인트를 넣으면 몇 XCN 나오는지 */
  getQuote(points) {
    return this._req('GET', '/exchange/quote' + this._q({ points }));
  }

  /** 전환 요청 */
  convert(points, idempotencyKey) {
    return this._req('POST', '/exchange/convert', {
      user_id: this.userId,
      points,
      idempotency_key: idempotencyKey || `${this.userId}-${Date.now()}`,
    });
  }

  /** 내 전환 내역 */
  getConversions(limit = 50) {
    return this._req('GET', '/exchange/conversions' + this._q({ user_id: this.userId, limit }));
  }

  /* ---------- 지갑 연동 (브라우저 / 웹뷰) ---------- */

  /**
   * 메타마스크 등 EIP-1193 지갑을 연결하고, 서명으로 소유를 증명해 서버에 등록합니다.
   * @param {object} [provider] 기본값 window.ethereum
   */
  async linkWallet(provider) {
    const eth = provider || (typeof window !== 'undefined' ? window.ethereum : null);
    if (!eth) {
      throw new Error('지갑을 찾을 수 없습니다. 메타마스크를 설치하거나 지갑 앱 브라우저에서 열어주세요.');
    }

    const accounts = await eth.request({ method: 'eth_requestAccounts' });
    const address = accounts[0];

    // 1) 서버에서 서명할 문구를 받는다
    const challenge = await this._req(
      'GET',
      '/wallet/nonce' + this._q({ user_id: this.userId })
    );

    // 2) 지갑으로 서명한다 (개인키는 절대 밖으로 나가지 않는다)
    const signature = await eth.request({
      method: 'personal_sign',
      params: [challenge.message, address],
    });

    // 3) 서버가 서명에서 주소를 복구해 확인한다
    return this._req('POST', '/wallet/link', {
      user_id: this.userId,
      address,
      signature,
    });
  }

  /** 유저 지갑 목록에 Xcoin 을 자동으로 추가해준다. */
  async addTokenToWallet(provider) {
    const eth = provider || (typeof window !== 'undefined' ? window.ethereum : null);
    if (!eth) throw new Error('지갑을 찾을 수 없습니다.');

    const cfg = await this.getConfig();
    if (!cfg.coin.contract) {
      throw new Error('아직 토큰 컨트랙트가 배포되지 않았습니다. (시뮬레이션 모드)');
    }
    return eth.request({
      method: 'wallet_watchAsset',
      params: {
        type: 'ERC20',
        options: {
          address: cfg.coin.contract,
          symbol: cfg.coin.symbol,
          decimals: cfg.coin.decimals,
        },
      },
    });
  }
}

/* ============================================================
 * 내보내기 — Node / 브라우저 양쪽 지원
 * ============================================================ */

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { XcoinBackend, XcoinClient };
}
if (typeof window !== 'undefined') {
  window.XcoinClient = XcoinClient;
}
