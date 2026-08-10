const alertBox = document.getElementById("alert-box");

function showAlert(message, kind = "error") {
  alertBox.innerHTML = `<div class="alert alert-${kind}">${message}</div>`;
}

function clearAlert() {
  alertBox.innerHTML = "";
}

// ── 탭 전환 ──────────────────────────────────
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");
    clearAlert();
  });
});

// ── 이미 로그인돼 있으면 바로 가이드 페이지로 ──────
fetch("/api/me", { credentials: "same-origin" }).then((res) => {
  if (res.ok) window.location.href = "/app/guide.html";
});

// ── 회원가입 발전소 목록 불러오기 ──────────────
const plantSelect = document.getElementById("signup-plant");
const signupSubmit = document.getElementById("signup-submit");

fetch("/api/plants", { credentials: "same-origin" })
  .then((res) => res.json())
  .then((data) => {
    if (data.error) {
      showAlert(`⚠️ DB 연결에 실패해 발전소 목록을 못 불러왔어요.<br>오류: ${data.error}`, "error");
      signupSubmit.disabled = true;
      return;
    }
    if (!data.plants || data.plants.length === 0) {
      showAlert(
        "아직 DB에 발전소별 기준이 없어요. refresh_komipo_data.py 또는 refresh_cleansys_data.py를 먼저 실행해주세요.",
        "warn"
      );
      signupSubmit.disabled = true;
      return;
    }
    plantSelect.innerHTML = data.plants.map((p) => `<option value="${p}">${p}</option>`).join("");
  })
  .catch(() => {
    showAlert("발전소 목록을 불러오는 중 오류가 발생했어요.", "error");
    signupSubmit.disabled = true;
  });

// ── 로그인 제출 ──────────────────────────────
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearAlert();
  const username = document.getElementById("login-username").value;
  const password = document.getElementById("login-password").value;

  const res = await fetch("/api/login", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (res.ok) {
    window.location.href = "/app/guide.html";
  } else {
    const err = await res.json().catch(() => ({}));
    showAlert(err.detail || "아이디 또는 비밀번호가 올바르지 않습니다.");
  }
});

// ── 회원가입 제출 ─────────────────────────────
document.getElementById("signup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearAlert();
  const username = document.getElementById("signup-username").value;
  const password = document.getElementById("signup-password").value;
  const plant_name = plantSelect.value;

  const res = await fetch("/api/signup", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, plant_name }),
  });
  const data = await res.json();

  if (data.ok) {
    showAlert("회원가입 완료! 위 '로그인' 탭에서 로그인해주세요.", "success");
    document.getElementById("signup-form").reset();
  } else {
    showAlert(data.error || "회원가입에 실패했어요.");
  }
});
