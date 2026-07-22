/* webauthn.js — passkey ceremonies over navigator.credentials. No build step.
 * CSRF: every fetch sends X-CSRF-Token from <meta name="csrf-token">.
 */
(function () {
  "use strict";
  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : "";
  }
  function b64urlToBuf(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/");
    var pad = s.length % 4 ? "=".repeat(4 - (s.length % 4)) : "";
    var bin = atob(s + pad), buf = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    return buf.buffer;
  }
  function bufToB64url(buf) {
    var bytes = new Uint8Array(buf), bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function post(url, body) {
    var opts = {
      method: "POST",
      headers: { "X-CSRF-Token": csrf(), "Accept": "application/json" },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (resp) {
      if (resp.status !== 401) return resp;
      return resp.clone().json().then(function (data) {
        if (data && data.error === "step_up_required" && data.redirect) {
          window.location.assign(data.redirect);
          return new Promise(function () {}); // navigation takes over
        }
        return resp;
      }, function () { return resp; });
    });
  }
  function encodeRegistration(cred) {
    return {
      id: cred.id, rawId: bufToB64url(cred.rawId), type: cred.type,
      response: {
        clientDataJSON: bufToB64url(cred.response.clientDataJSON),
        attestationObject: bufToB64url(cred.response.attestationObject),
        transports: cred.response.getTransports ? cred.response.getTransports() : [],
      },
    };
  }
  function encodeAssertion(cred) {
    return {
      id: cred.id, rawId: bufToB64url(cred.rawId), type: cred.type,
      response: {
        clientDataJSON: bufToB64url(cred.response.clientDataJSON),
        authenticatorData: bufToB64url(cred.response.authenticatorData),
        signature: bufToB64url(cred.response.signature),
        userHandle: cred.response.userHandle ? bufToB64url(cred.response.userHandle) : null,
      },
    };
  }
  async function register(nickname) {
    var optsResp = await post("/account/security/passkey/options");
    if (!optsResp.ok) throw new Error("options request failed");
    var options = await optsResp.json();
    options.challenge = b64urlToBuf(options.challenge);
    options.user.id = b64urlToBuf(options.user.id);
    (options.excludeCredentials || []).forEach(function (c) { c.id = b64urlToBuf(c.id); });
    var cred = await navigator.credentials.create({ publicKey: options });
    var verifyResp = await post("/account/security/passkey/verify",
      { credential: encodeRegistration(cred), nickname: nickname || "Passkey" });
    if (!verifyResp.ok) throw new Error("verification failed");
    window.location.assign("/account/security");
  }
  async function assertionCeremony(optionsUrl, verifyUrl, extra) {
    var optsResp = await post(optionsUrl);
    if (!optsResp.ok) throw new Error("options request failed");
    var options = await optsResp.json();
    options.challenge = b64urlToBuf(options.challenge);
    (options.allowCredentials || []).forEach(function (c) { c.id = b64urlToBuf(c.id); });
    var cred = await navigator.credentials.get({ publicKey: options });
    var body = Object.assign({ credential: encodeAssertion(cred) }, extra || {});
    var verifyResp = await post(verifyUrl, body);
    if (!verifyResp.ok) throw new Error("verification failed");
    return verifyResp.json();
  }
  async function authenticate() {
    var data = await assertionCeremony("/login/passkey/options", "/login/passkey/verify");
    window.location.assign(data.next || "/");
  }
  async function stepUp(next) {
    var data = await assertionCeremony(
      "/auth/step-up/passkey/options", "/auth/step-up/passkey/verify", { next: next || "/" });
    window.location.assign(data.next || "/");
  }
  window.idraaWebAuthn = { register: register, authenticate: authenticate, stepUp: stepUp };
})();
