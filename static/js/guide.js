fetch("/api/me", { credentials: "same-origin" })
  .then((res) => {
    if (!res.ok) {
      window.location.href = "/app/login.html";
      throw new Error("not logged in");
    }
    return res.json();
  })
  .then((user) => {
    document.getElementById("plant-chip").textContent = `🏭 ${user.plant_name}`;
    document.getElementById("username-label").textContent = `${user.username}님으로 로그인됨`;
    document.getElementById("plant-inline").textContent = user.plant_name;
  })
  .catch(() => {});

document.getElementById("start-btn").addEventListener("click", () => {
  window.location.href = "/app/chat.html";
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  window.location.href = "/app/login.html";
});
