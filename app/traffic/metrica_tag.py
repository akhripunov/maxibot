from __future__ import annotations

import html


def valid_counter_id(value: str | int | None) -> str | None:
    """Return a positive numeric counter id, or fail closed."""
    normalized = str(value or "").strip()
    if not normalized.isdigit() or int(normalized) <= 0:
        return None
    return normalized


def metrica_tag_html(counter_id: str | int | None) -> str:
    """Render only a channel-scoped Metrika tag; empty when not configured."""
    counter = valid_counter_id(counter_id)
    if counter is None:
        return ""
    safe_counter = html.escape(counter, quote=True)
    return f'''<script type="text/javascript">
(function(m,e,t,r,i,k,a){{
m[i]=m[i]||function(){{(m[i].a=m[i].a||[]).push(arguments)}};
m[i].l=1*new Date();
var maxibotMetrikaAttempts=0;
function loadMaxibotMetrika(){{
  k=e.createElement(t);
  k.async=1;
  k.src=r + (maxibotMetrikaAttempts ? "?_mb_retry=" + maxibotMetrikaAttempts : "");
  k.onload=function(){{window.dispatchEvent(new Event("maxibot-metrica-ready"));}};
  k.onerror=function(){{
    k.remove();
    if (maxibotMetrikaAttempts < 2) {{
      maxibotMetrikaAttempts += 1;
      window.setTimeout(loadMaxibotMetrika, 150 * maxibotMetrikaAttempts);
      return;
    }}
    window.dispatchEvent(new CustomEvent("maxibot-metrica-error", {{detail: {{attempts: maxibotMetrikaAttempts + 1}}}}));
  }};
  a=(e.getElementsByTagName("head")[0] || e.getElementsByTagName("body")[0] || e.documentElement);
  a.appendChild(k);
}}
loadMaxibotMetrika()
}})(window, document, "script", "https://mc.yandex.ru/metrika/tag.js", "ym");
ym({safe_counter}, "init", {{defer:true, clickmap:true, trackLinks:true, accurateTrackBounce:true, webvisor:true}});
// The landing runtime persists this browser-generated ID through a dedicated
// endpoint.  It is never added to diagnostics or browser event metadata.
window.addEventListener("maxibot-metrica-ready", function(){{
  try {{
    ym({safe_counter}, "getClientID", function(clientId){{
      window.dispatchEvent(new CustomEvent("maxibot-metrica-client-id", {{detail: {{clientId: String(clientId || "")}}}}));
    }});
  }} catch (_) {{}}
}}, {{once:true}});
</script>'''
