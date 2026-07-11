/**
 * TradingView Webhook → Telegram 轉發器(Cloudflare Worker,免費額度足夠)
 * ─────────────────────────────────────────────
 * 部署:
 *   1. dash.cloudflare.com → Workers → Create → 貼上本檔
 *   2. Settings → Variables 加入 Secrets:
 *        TG_TOKEN  = Telegram Bot Token
 *        TG_CHAT   = Chat ID
 *        SECRET    = 自訂一段亂碼(防止陌生人打你的 webhook)
 *   3. TradingView 警報 Webhook URL 填:
 *        https://<你的worker>.workers.dev/?key=<SECRET>
 *
 * Pine 端 alert() 送出的 JSON(本平台 Pine 產生器內建):
 *   {"ticker":"2330","action":"BUY","price":1050,"time":"...","strategy":"ma_cross"}
 * 純文字警報也能收,會原文轉發。
 */
export default {
  async fetch(request, env) {
    if (request.method !== "POST")
      return new Response("Quant Lab webhook OK", { status: 200 });

    const url = new URL(request.url);
    if (env.SECRET && url.searchParams.get("key") !== env.SECRET)
      return new Response("forbidden", { status: 403 });

    const raw = await request.text();
    let text;
    try {
      const j = JSON.parse(raw);
      const icon = j.action === "BUY" ? "🔴" : j.action === "SELL" ? "🟢" : "📡";
      text =
        `${icon} <b>TradingView 訊號</b>\n` +
        `${j.ticker || "?"} — ${j.action || "?"} @ ${j.price ?? "?"}\n` +
        `策略:${j.strategy || "-"}\n${j.time || ""}`;
    } catch {
      text = "📡 TradingView 警報:\n" + raw.slice(0, 500);
    }

    const r = await fetch(
      `https://api.telegram.org/bot${env.TG_TOKEN}/sendMessage`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: env.TG_CHAT, text, parse_mode: "HTML" }),
      }
    );
    return new Response(r.ok ? "sent" : "telegram error", {
      status: r.ok ? 200 : 502,
    });
  },
};
