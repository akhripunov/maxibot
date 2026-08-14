const out = document.getElementById("out");
document.getElementById("scan").addEventListener("click", async () => {
  out.textContent = "Scanning read-only targets...";
  const result = await chrome.runtime.sendMessage({type: "scan"});
  out.textContent = JSON.stringify(result, null, 2);
});
