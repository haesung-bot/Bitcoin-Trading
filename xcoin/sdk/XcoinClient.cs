/*
 * XcoinClient.cs — Unity 용 Xcoin 클라이언트
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 *
 * 유니티로 만든 퍼즐게임에 붙이는 쪽입니다.
 *
 * ★ 여기에 게임 secret 을 넣지 마세요 ★
 * 유니티 빌드는 뜯으면 문자열이 다 보입니다. 점수 제출 서명은 반드시
 * 여러분의 게임 백엔드에서 해야 합니다. 이 클래스는
 *   - 백엔드에서 받은 user_token 으로 잔액/전환을 조회하고
 *   - 판이 끝나면 백엔드에 결과를 넘기는
 * 역할만 합니다.
 *
 * 사용 예:
 *
 *   var xcoin = gameObject.AddComponent<XcoinClient>();
 *   xcoin.serverUrl = "https://xcoin.example.com";
 *   xcoin.backendUrl = "https://my-game-backend.example.com";
 *
 *   // 로그인 직후
 *   StartCoroutine(xcoin.Login("user_123", token => Debug.Log("토큰 발급 완료")));
 *
 *   // 한 판 끝났을 때
 *   StartCoroutine(xcoin.SubmitScore(3200, 47500, res => Debug.Log(res)));
 *
 *   // 포인트 화면 열 때
 *   StartCoroutine(xcoin.GetBalance(b => pointText.text = b.points + " P"));
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 */

using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace Xcoin
{
    [Serializable]
    public class BalanceResponse
    {
        public bool ok;
        public string user_id;
        public int points;
        public int lifetime_earned;
        public string convertible_xcoin;
    }

    [Serializable]
    public class SubmitResponse
    {
        public bool ok;
        public int awarded;
        public bool duplicate;
        public int points;
        public bool capped;
    }

    [Serializable]
    public class WalletResponse
    {
        public bool ok;
        public bool linked;
        public string address;
        public int chain_id;
    }

    [Serializable]
    public class ConvertResponse
    {
        public bool ok;
        public int id;
        public string status;
        public int points_spent;
        public string amount;
        public string symbol;
        public string tx_hash;
    }

    [Serializable]
    public class ErrorResponse
    {
        public bool ok;
        public string error;
        public string message;
    }

    public class XcoinClient : MonoBehaviour
    {
        [Header("Xcoin 서버 주소")]
        public string serverUrl = "http://localhost:8080";

        [Header("내 게임 백엔드 주소 (점수 서명을 대신해 주는 곳)")]
        public string backendUrl = "";

        [Header("자동 채워짐")]
        public string userId = "";
        public string userToken = "";

        public bool IsLoggedIn => !string.IsNullOrEmpty(userToken);

        // ===================== 로그인 =====================

        /// <summary>
        /// 게임 백엔드에 로그인해서 Xcoin 유저 토큰을 받아온다.
        /// 백엔드는 POST {backendUrl}/xcoin/token 을 열어두고,
        /// 그 안에서 XcoinBackend.issueUserToken(userId) 를 호출해 결과를 그대로 돌려주면 된다.
        /// </summary>
        public IEnumerator Login(string uid, Action<string> onSuccess = null,
                                 Action<string> onError = null)
        {
            userId = uid;
            string body = "{\"user_id\":\"" + Escape(uid) + "\"}";

            using (var req = MakeRequest(backendUrl + "/xcoin/token", "POST", body, false))
            {
                yield return req.SendWebRequest();
                if (!IsOk(req))
                {
                    onError?.Invoke(ErrorOf(req));
                    yield break;
                }

                var parsed = JsonUtility.FromJson<TokenResponse>(req.downloadHandler.text);
                userToken = parsed.user_token;
                onSuccess?.Invoke(userToken);
            }
        }

        [Serializable]
        private class TokenResponse
        {
            public bool ok;
            public string user_id;
            public string user_token;
        }

        // ===================== 점수 제출 =====================

        /// <summary>
        /// 한 판이 끝나면 호출. 실제 서명과 제출은 백엔드가 한다.
        /// 백엔드는 POST {backendUrl}/xcoin/score 를 열어두고
        /// XcoinBackend.submitScore(userId, score, durationMs, nonce) 를 호출하면 된다.
        ///
        /// nonce 는 이 판의 고유 ID. 네트워크가 끊겨 재시도할 때 같은 값을 보내면
        /// 포인트가 두 번 들어가지 않는다.
        /// </summary>
        public IEnumerator SubmitScore(int score, int durationMs,
                                       Action<SubmitResponse> onSuccess = null,
                                       Action<string> onError = null,
                                       string nonce = null)
        {
            if (string.IsNullOrEmpty(userId))
            {
                onError?.Invoke("먼저 Login 을 호출하세요.");
                yield break;
            }

            string n = string.IsNullOrEmpty(nonce) ? Guid.NewGuid().ToString("N") : nonce;
            string body = "{\"user_id\":\"" + Escape(userId) + "\"," +
                          "\"score\":" + score + "," +
                          "\"duration_ms\":" + durationMs + "," +
                          "\"nonce\":\"" + Escape(n) + "\"}";

            using (var req = MakeRequest(backendUrl + "/xcoin/score", "POST", body, false))
            {
                yield return req.SendWebRequest();
                if (!IsOk(req))
                {
                    onError?.Invoke(ErrorOf(req));
                    yield break;
                }
                onSuccess?.Invoke(JsonUtility.FromJson<SubmitResponse>(req.downloadHandler.text));
            }
        }

        // ===================== 조회 =====================

        public IEnumerator GetBalance(Action<BalanceResponse> onSuccess = null,
                                      Action<string> onError = null)
        {
            string url = serverUrl + "/points/balance?user_id=" + UnityWebRequest.EscapeURL(userId);
            using (var req = MakeRequest(url, "GET", null, true))
            {
                yield return req.SendWebRequest();
                if (!IsOk(req)) { onError?.Invoke(ErrorOf(req)); yield break; }
                onSuccess?.Invoke(JsonUtility.FromJson<BalanceResponse>(req.downloadHandler.text));
            }
        }

        public IEnumerator GetWallet(Action<WalletResponse> onSuccess = null,
                                     Action<string> onError = null)
        {
            string url = serverUrl + "/wallet?user_id=" + UnityWebRequest.EscapeURL(userId);
            using (var req = MakeRequest(url, "GET", null, true))
            {
                yield return req.SendWebRequest();
                if (!IsOk(req)) { onError?.Invoke(ErrorOf(req)); yield break; }
                onSuccess?.Invoke(JsonUtility.FromJson<WalletResponse>(req.downloadHandler.text));
            }
        }

        // ===================== 전환 =====================

        /// <summary>
        /// 포인트를 Xcoin 으로 바꾼다. 지갑이 먼저 연동되어 있어야 한다.
        /// idempotencyKey 는 이 요청의 고유 이름. 재시도 시 같은 값을 쓰면 중복 전환이 없다.
        /// </summary>
        public IEnumerator Convert(int points, Action<ConvertResponse> onSuccess = null,
                                   Action<string> onError = null, string idempotencyKey = null)
        {
            string key = string.IsNullOrEmpty(idempotencyKey)
                ? userId + "-" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                : idempotencyKey;

            string body = "{\"user_id\":\"" + Escape(userId) + "\"," +
                          "\"points\":" + points + "," +
                          "\"idempotency_key\":\"" + Escape(key) + "\"}";

            using (var req = MakeRequest(serverUrl + "/exchange/convert", "POST", body, true))
            {
                yield return req.SendWebRequest();
                if (!IsOk(req)) { onError?.Invoke(ErrorOf(req)); yield break; }
                onSuccess?.Invoke(JsonUtility.FromJson<ConvertResponse>(req.downloadHandler.text));
            }
        }

        /// <summary>
        /// 지갑 연동 페이지를 외부 브라우저로 연다.
        /// 모바일 앱 안에서는 메타마스크에 직접 서명을 요청하기 어려우므로,
        /// 서버가 제공하는 웹 페이지에서 처리하게 하는 편이 가장 간단하다.
        /// (WalletConnect 를 붙이면 앱 안에서도 가능하지만 설정이 훨씬 복잡하다.)
        /// </summary>
        public void OpenWalletLinkPage()
        {
            string url = serverUrl + "/demo?user_id=" + UnityWebRequest.EscapeURL(userId) +
                         "&user_token=" + UnityWebRequest.EscapeURL(userToken);
            Application.OpenURL(url);
        }

        // ===================== 내부 도우미 =====================

        private UnityWebRequest MakeRequest(string url, string method, string body, bool withToken)
        {
            var req = new UnityWebRequest(url, method);
            req.downloadHandler = new DownloadHandlerBuffer();
            if (!string.IsNullOrEmpty(body))
            {
                req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
                req.SetRequestHeader("Content-Type", "application/json");
            }
            if (withToken && !string.IsNullOrEmpty(userToken))
            {
                req.SetRequestHeader("X-User-Token", userToken);
            }
            req.timeout = 20;
            return req;
        }

        private static bool IsOk(UnityWebRequest req)
        {
            return req.result == UnityWebRequest.Result.Success;
        }

        private static string ErrorOf(UnityWebRequest req)
        {
            string text = req.downloadHandler != null ? req.downloadHandler.text : "";
            if (!string.IsNullOrEmpty(text))
            {
                try
                {
                    var err = JsonUtility.FromJson<ErrorResponse>(text);
                    if (!string.IsNullOrEmpty(err.message)) return err.message;
                }
                catch (Exception) { /* JSON 이 아니면 아래로 */ }
            }
            return req.error ?? "알 수 없는 오류";
        }

        private static string Escape(string s)
        {
            return (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
        }
    }
}
