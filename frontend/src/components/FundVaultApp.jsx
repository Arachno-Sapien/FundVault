"use client";

import { useEffect, useMemo, useState } from "react";

import AuthView from "components/auth/AuthView";
import HeaderBar from "components/layout/HeaderBar";
import NavTabs from "components/layout/NavTabs";
import AppModals from "components/modals/AppModals";
import AuditView from "components/views/AuditView";
import DashboardView from "components/views/DashboardView";
import DatabaseView from "components/views/DatabaseView";
import HomeView from "components/views/HomeView";
import TrashView from "components/views/TrashView";
import { apiRequest } from "lib/api";
import { fmt, formatDate, nowInput } from "lib/format";

const defaultTxnFilters = {
  search: "",
  type: "",
  mode: "",
  dateFrom: "",
  dateTo: "",
  amountMin: "",
  amountMax: ""
};

export default function FundVaultApp() {
  const [theme, setTheme] = useState("dark");
  const [authTab, setAuthTab] = useState("login");
  const [activeTab, setActiveTab] = useState("home");
  const [token, setToken] = useState("");
  const [currentUser, setCurrentUser] = useState(null);
  const [userDropdownOpen, setUserDropdownOpen] = useState(false);

  const [loginForm, setLoginForm] = useState({ username: "", password: "" });
  const [signupForm, setSignupForm] = useState({ username: "", email: "", password: "", confirm: "" });
  const [profileForm, setProfileForm] = useState({
    username: "",
    email: "",
    currentPassword: "",
    newPassword: "",
    confirmPassword: "",
    profileImage: ""
  });

  const [databases, setDatabases] = useState([]);
  const [currentDbId, setCurrentDbId] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [trashItems, setTrashItems] = useState([]);
  const [recurringItems, setRecurringItems] = useState([]);
  const [managedUsers, setManagedUsers] = useState([]);
  const [overview, setOverview] = useState(null);

  const [txnFilters, setTxnFilters] = useState(defaultTxnFilters);
  const [auditFilters, setAuditFilters] = useState({ search: "", action: "" });

  const [createDbForm, setCreateDbForm] = useState({ name: "", description: "", lowBalanceThreshold: "", approvalThreshold: "" });
  const [editDbForm, setEditDbForm] = useState({ name: "", description: "", lowBalanceThreshold: "", approvalThreshold: "" });
  const [txnForm, setTxnForm] = useState({
    type: "",
    amount: "",
    date: nowInput(),
    sender: "",
    receiver: "",
    mode: "electronic",
    modeData: { elecId: "" },
    location: "",
    notes: "",
    receiptImage: null
  });
  const [editTxnForm, setEditTxnForm] = useState({ id: "", amount: "", date: "", sender: "", receiver: "", location: "", notes: "" });
  const [voidTransactionId, setVoidTransactionId] = useState("");
  const [voidReason, setVoidReason] = useState("");
  const [mergeForm, setMergeForm] = useState({ sourceId: "", targetId: "", name: "" });
  const [recurringForm, setRecurringForm] = useState({ type: "", amount: "", frequency: "monthly", description: "", nextRun: new Date().toISOString().slice(0, 10) });
  const [exportForm, setExportForm] = useState({ dateFrom: "", dateTo: "", orgName: "" });
  const [selectedReceipt, setSelectedReceipt] = useState(null);

  const [modals, setModals] = useState({
    createDb: false,
    editDb: false,
    txn: false,
    editTxn: false,
    voidTxn: false,
    merge: false,
    recurring: false,
    export: false,
    receipt: false,
    profile: false,
    userManagement: false,
    shortcuts: false
  });

  const [toasts, setToasts] = useState([]);
  const currentDb = useMemo(() => databases.find(db => db.id === currentDbId) || null, [databases, currentDbId]);

  const toast = (message, type = "info") => {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000);
  };

  const authedRequest = (endpoint, options = {}) => apiRequest(`/api${endpoint}`, options, token);

  const refreshOverview = async () => {
    if (!token) return;
    try {
      const data = await authedRequest("/analytics/overview");
      setOverview(data);
    } catch (_err) {
      setOverview(null);
    }
  };

  const hydrateDatabases = async () => {
    const list = await authedRequest("/databases");
    const hydrated = await Promise.all(
      list.map(async db => {
        try {
          const detail = await authedRequest(`/databases/${db.id}`);
          return { ...db, transactions: detail.transactions || [] };
        } catch (_err) {
          return { ...db, transactions: [] };
        }
      })
    );
    setDatabases(hydrated);
  };

  const refreshAudit = async () => {
    try {
      setAuditLogs(await authedRequest("/audit"));
    } catch (_err) {
      setAuditLogs([]);
    }
  };

  const refreshTrash = async () => {
    try {
      setTrashItems(await authedRequest("/trash"));
    } catch (_err) {
      setTrashItems([]);
    }
  };

  const refreshManagedUsers = async () => {
    try {
      const users = await authedRequest("/admin/users");
      setManagedUsers(users);
    } catch (err) {
      toast(err.message, "error");
      setManagedUsers([]);
    }
  };

  const openProfileModal = () => {
    setProfileForm({
      username: currentUser?.username || "",
      email: currentUser?.email || "",
      currentPassword: "",
      newPassword: "",
      confirmPassword: "",
      profileImage: currentUser?.profile_image || ""
    });
    setModals(prev => ({ ...prev, profile: true }));
  };

  const updateProfileImage = file => {
    if (!file) {
      setProfileForm(prev => ({ ...prev, profileImage: "" }));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setProfileForm(prev => ({ ...prev, profileImage: String(reader.result || "") }));
    };
    reader.readAsDataURL(file);
  };

  const saveProfile = async () => {
    if (!profileForm.username.trim() || !profileForm.email.trim()) {
      toast("Username and email are required", "error");
      return;
    }
    const hasPasswordUpdate =
      profileForm.currentPassword || profileForm.newPassword || profileForm.confirmPassword;
    if (hasPasswordUpdate) {
      if (!profileForm.currentPassword || !profileForm.newPassword || !profileForm.confirmPassword) {
        toast("Fill all password fields to update your password", "error");
        return;
      }
      if (profileForm.newPassword.length < 6) {
        toast("Password must be at least 6 characters", "error");
        return;
      }
      if (profileForm.newPassword !== profileForm.confirmPassword) {
        toast("Passwords do not match", "error");
        return;
      }
    }

    try {
      const updated = await authedRequest("/auth/me", {
        method: "PUT",
        body: JSON.stringify({
          username: profileForm.username.trim(),
          email: profileForm.email.trim(),
          profile_image: profileForm.profileImage,
          currentPassword: profileForm.currentPassword,
          newPassword: profileForm.newPassword,
          confirmPassword: profileForm.confirmPassword
        })
      });
      const nextUser = { ...updated, token };
      setCurrentUser(nextUser);
      localStorage.setItem("fundvault_currentUser", JSON.stringify(updated));
      setProfileForm(prev => ({
        ...prev,
        currentPassword: "",
        newPassword: "",
        confirmPassword: ""
      }));
      setModals(prev => ({ ...prev, profile: false }));
      toast("Profile updated", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const loadCurrentDb = async dbId => {
    const detail = await authedRequest(`/databases/${dbId}`);
    setDatabases(prev => prev.map(db => (db.id === dbId ? { ...db, ...detail, transactions: detail.transactions || [] } : db)));
    setTransactions(detail.transactions || []);
    setCurrentDbId(dbId);
    setActiveTab("db");
  };

  const loadRecurring = async dbId => {
    try {
      const items = await authedRequest(`/databases/${dbId}/recurring`);
      setRecurringItems(items);
    } catch (_err) {
      setRecurringItems([]);
    }
  };

  const runPostLoginLoad = async () => {
    try {
      await authedRequest("/recurring/process", { method: "POST" });
    } catch (_err) {
      // ignore
    }
    await Promise.all([hydrateDatabases(), refreshAudit(), refreshTrash(), refreshOverview()]);
  };

  const handleLogin = async () => {
    if (!loginForm.username || !loginForm.password) {
      toast("Please fill in all fields", "error");
      return;
    }
    try {
      const response = await apiRequest("/api/auth/login", { method: "POST", body: JSON.stringify(loginForm) });
      setToken(response.token);
      setCurrentUser({ ...response.user, token: response.token });
      localStorage.setItem("fundvault_token", response.token);
      localStorage.setItem("fundvault_currentUser", JSON.stringify(response.user));
      setLoginForm({ username: "", password: "" });
      toast(`Welcome back, ${response.user.username}!`, "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const handleSignup = async () => {
    if (!signupForm.username || !signupForm.email || !signupForm.password || !signupForm.confirm) {
      toast("Please fill in all fields", "error");
      return;
    }
    if (signupForm.password !== signupForm.confirm) {
      toast("Passwords do not match", "error");
      return;
    }
    if (signupForm.password.length < 6) {
      toast("Password must be at least 6 characters", "error");
      return;
    }

    try {
      const response = await apiRequest("/api/auth/signup", {
        method: "POST",
        body: JSON.stringify({ username: signupForm.username, email: signupForm.email, password: signupForm.password })
      });
      setToken(response.token);
      setCurrentUser({ ...response.user, token: response.token });
      localStorage.setItem("fundvault_token", response.token);
      localStorage.setItem("fundvault_currentUser", JSON.stringify(response.user));
      setSignupForm({ username: "", email: "", password: "", confirm: "" });
      toast(`Account created! Welcome, ${response.user.username}!`, "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const logout = async () => {
    const logoutToken = token;
    setToken("");
    setCurrentUser(null);
    setDatabases([]);
    setCurrentDbId(null);
    setTransactions([]);
    setAuditLogs([]);
    setTrashItems([]);
    setRecurringItems([]);
    setManagedUsers([]);
    setOverview(null);
    setActiveTab("home");
    localStorage.removeItem("fundvault_token");
    localStorage.removeItem("fundvault_currentUser");
    if (logoutToken) {
      try {
        await apiRequest("/api/auth/logout", { method: "POST" }, logoutToken);
      } catch (_err) {
        // ignore
      }
    }
    toast("Logged out successfully", "success");
  };

  const createDatabase = async () => {
    if (!createDbForm.name.trim()) {
      toast("Please enter a database name", "error");
      return;
    }
    try {
      const created = await authedRequest("/databases", {
        method: "POST",
        body: JSON.stringify({
          name: createDbForm.name.trim(),
          description: createDbForm.description.trim(),
          lowBalanceThreshold: Number(createDbForm.lowBalanceThreshold || 0),
          approvalThreshold: Number(createDbForm.approvalThreshold || 0)
        })
      });
      setModals(prev => ({ ...prev, createDb: false }));
      setCreateDbForm({ name: "", description: "", lowBalanceThreshold: "", approvalThreshold: "" });
      toast(`Database "${created.name}" created successfully`, "success");
      await hydrateDatabases();
      await refreshAudit();
      await refreshOverview();
      await loadCurrentDb(created.id);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const saveDatabaseEdit = async () => {
    if (!currentDbId) return;
    if (!editDbForm.name.trim()) {
      toast("Please enter a database name", "error");
      return;
    }
    try {
      await authedRequest(`/databases/${currentDbId}`, {
        method: "PUT",
        body: JSON.stringify({
          name: editDbForm.name.trim(),
          description: editDbForm.description.trim(),
          lowBalanceThreshold: Number(editDbForm.lowBalanceThreshold || 0),
          approvalThreshold: Number(editDbForm.approvalThreshold || 0)
        })
      });
      setModals(prev => ({ ...prev, editDb: false }));
      toast("Database updated", "success");
      await hydrateDatabases();
      await refreshAudit();
      await refreshOverview();
      if (activeTab === "db") await loadCurrentDb(currentDbId);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const deleteDatabase = async dbId => {
    const db = databases.find(item => item.id === dbId);
    if (!db) return;
    if (!window.confirm(`Are you sure you want to delete "${db.name}"? It will be moved to trash.`)) return;
    try {
      await authedRequest(`/databases/${dbId}`, { method: "DELETE" });
      toast("Database moved to trash", "info");
      if (currentDbId === dbId) {
        setCurrentDbId(null);
        setTransactions([]);
        setActiveTab("home");
      }
      await Promise.all([hydrateDatabases(), refreshAudit(), refreshTrash(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const archiveDatabase = async () => {
    if (!currentDbId) return;
    try {
      const response = await authedRequest(`/databases/${currentDbId}/archive`, { method: "POST" });
      toast(response.is_archived ? "Database archived" : "Database unarchived", "success");
      await Promise.all([hydrateDatabases(), refreshAudit(), refreshOverview(), loadCurrentDb(currentDbId)]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const submitTransaction = async () => {
    if (!currentDbId) return;
    if (!txnForm.type) {
      toast("Select Credit or Debit", "error");
      return;
    }
    const activeTransactionCount = transactions.filter(txn => !txn.is_voided).length;
    if (activeTransactionCount === 0 && txnForm.type !== "credit") {
      toast("First transaction must be a Credit", "error");
      return;
    }
    const amount = Number(txnForm.amount);
    if (!amount || amount <= 0) {
      toast("Enter a valid amount", "error");
      return;
    }
    const parsedDate = txnForm.date ? new Date(txnForm.date) : new Date();
    if (Number.isNaN(parsedDate.getTime())) {
      toast("Please provide a valid transaction date", "error");
      return;
    }
    if (txnForm.mode === "electronic" && !txnForm.modeData.elecId?.trim()) {
      toast("Enter Transaction ID for electronic payment", "error");
      return;
    }
    if (
      txnForm.mode === "cheque" &&
      (!txnForm.modeData.chequeNo?.trim() || !txnForm.modeData.chequeDate || !txnForm.modeData.chequeBank?.trim())
    ) {
      toast("Fill all cheque details", "error");
      return;
    }
    try {
      const response = await authedRequest(`/databases/${currentDbId}/transactions`, {
        method: "POST",
        body: JSON.stringify({
          type: txnForm.type,
          amount,
          date: parsedDate.toISOString(),
          sender: txnForm.sender.trim(),
          receiver: txnForm.receiver.trim(),
          mode: txnForm.mode,
          modeData: txnForm.modeData,
          location: txnForm.location.trim(),
          notes: txnForm.notes.trim(),
          receiptImage: txnForm.receiptImage
        })
      });
      setModals(prev => ({ ...prev, txn: false }));
      setTxnForm({
        type: "",
        amount: "",
        date: nowInput(),
        sender: "",
        receiver: "",
        mode: "electronic",
        modeData: { elecId: "" },
        location: "",
        notes: "",
        receiptImage: null
      });
      toast(
        response.requiresApproval
          ? `Transaction pending approval${currentDb?.approval_threshold ? ` (above ${fmt(currentDb.approval_threshold)} threshold)` : ""}`
          : `${txnForm.type === "credit" ? "Credit" : "Debit"} of ${fmt(amount)} recorded`,
        response.requiresApproval ? "warning" : "success"
      );
      await Promise.all([loadCurrentDb(currentDbId), hydrateDatabases(), refreshAudit(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const saveTransactionEdit = async () => {
    if (!editTxnForm.id) return;
    const amount = Number(editTxnForm.amount);
    if (!amount || amount <= 0) {
      toast("Enter a valid amount", "error");
      return;
    }
    const parsedDate = new Date(editTxnForm.date);
    if (Number.isNaN(parsedDate.getTime())) {
      toast("Please provide a valid transaction date", "error");
      return;
    }
    try {
      await authedRequest(`/transactions/${editTxnForm.id}`, {
        method: "PUT",
        body: JSON.stringify({
          amount,
          date: parsedDate.toISOString(),
          sender: editTxnForm.sender.trim(),
          receiver: editTxnForm.receiver.trim(),
          location: editTxnForm.location.trim(),
          notes: editTxnForm.notes.trim()
        })
      });
      setModals(prev => ({ ...prev, editTxn: false }));
      toast("Transaction updated", "success");
      await Promise.all([loadCurrentDb(currentDbId), hydrateDatabases(), refreshAudit(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const confirmVoidTransaction = async () => {
    if (!voidTransactionId) return;
    if (!voidReason.trim()) {
      toast("Please provide a reason for voiding", "error");
      return;
    }
    try {
      await authedRequest(`/transactions/${voidTransactionId}/void`, {
        method: "POST",
        body: JSON.stringify({ reason: voidReason.trim() })
      });
      setModals(prev => ({ ...prev, voidTxn: false }));
      setVoidReason("");
      setVoidTransactionId("");
      toast("Transaction voided", "info");
      await Promise.all([loadCurrentDb(currentDbId), hydrateDatabases(), refreshAudit(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const approveTransaction = async txnId => {
    try {
      await authedRequest(`/transactions/${txnId}/approve`, { method: "POST" });
      toast("Transaction approved", "success");
      await Promise.all([loadCurrentDb(currentDbId), hydrateDatabases(), refreshAudit(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const deleteVoidedTransaction = async txnId => {
    if (!confirm("Permanently delete this voided transaction? This cannot be undone.")) return;
    try {
      await authedRequest(`/transactions/${txnId}/delete`, { method: "DELETE" });
      toast("Voided transaction deleted", "info");
      await Promise.all([loadCurrentDb(currentDbId), hydrateDatabases(), refreshAudit(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const addRecurring = async () => {
    if (!currentDbId) return;
    if (!recurringForm.type) {
      toast("Select Credit or Debit", "error");
      return;
    }
    const amount = Number(recurringForm.amount);
    if (!amount || amount <= 0) {
      toast("Enter a valid amount", "error");
      return;
    }
    if (!recurringForm.description.trim()) {
      toast("Enter a description", "error");
      return;
    }
    try {
      await authedRequest(`/databases/${currentDbId}/recurring`, {
        method: "POST",
        body: JSON.stringify({
          type: recurringForm.type,
          amount,
          frequency: recurringForm.frequency,
          description: recurringForm.description.trim(),
          nextRun: recurringForm.nextRun
        })
      });
      toast("Recurring transaction added", "success");
      setRecurringForm({ type: "", amount: "", frequency: "monthly", description: "", nextRun: new Date().toISOString().slice(0, 10) });
      await loadRecurring(currentDbId);
      await refreshAudit();
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const deleteRecurring = async recurringId => {
    try {
      await authedRequest(`/recurring/${recurringId}`, { method: "DELETE" });
      toast("Recurring transaction removed", "info");
      await loadRecurring(currentDbId);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const mergeDatabases = async () => {
    if (!mergeForm.sourceId || !mergeForm.targetId || !mergeForm.name.trim()) {
      toast("Fill all merge fields", "error");
      return;
    }
    if (mergeForm.sourceId === mergeForm.targetId) {
      toast("Cannot merge a database with itself", "error");
      return;
    }
    try {
      await authedRequest("/databases/merge", {
        method: "POST",
        body: JSON.stringify({
          sourceId: mergeForm.sourceId,
          targetId: mergeForm.targetId,
          name: mergeForm.name.trim()
        })
      });
      setModals(prev => ({ ...prev, merge: false }));
      setMergeForm({ sourceId: "", targetId: "", name: "" });
      toast("Databases merged successfully", "success");
      await Promise.all([hydrateDatabases(), refreshAudit(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const restoreTrashItem = async itemId => {
    try {
      await authedRequest(`/trash/${itemId}/restore`, { method: "POST" });
      toast("Item restored", "success");
      await Promise.all([hydrateDatabases(), refreshAudit(), refreshTrash(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const permanentDeleteTrashItem = async itemId => {
    if (!window.confirm("Permanently delete? This cannot be undone.")) return;
    try {
      await authedRequest(`/trash/${itemId}`, { method: "DELETE" });
      toast("Permanently deleted", "info");
      await Promise.all([hydrateDatabases(), refreshTrash(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const emptyTrash = async () => {
    if (!window.confirm("Empty all trash? This cannot be undone.")) return;
    try {
      await authedRequest("/trash", { method: "DELETE" });
      toast("Trash emptied", "info");
      await Promise.all([hydrateDatabases(), refreshTrash(), refreshOverview()]);
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const editManagedUser = async user => {
    const username = window.prompt("Update username:", user.username);
    if (username === null) return;
    const email = window.prompt("Update email:", user.email);
    if (email === null) return;
    if (!username.trim() || !email.trim()) {
      toast("Username and email are required", "error");
      return;
    }
    try {
      const updated = await authedRequest(`/admin/users/${user.id}`, {
        method: "PUT",
        body: JSON.stringify({ username: username.trim(), email: email.trim() })
      });
      if (currentUser?.id === user.id) {
        const next = { ...currentUser, username: updated.username, email: updated.email };
        setCurrentUser(next);
        localStorage.setItem("fundvault_currentUser", JSON.stringify(updated));
      }
      toast("User updated", "success");
      await refreshManagedUsers();
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const toggleManagedUserRole = async user => {
    const nextRole = user.role === "admin" ? "member" : "admin";
    if (!window.confirm(`Change role of ${user.username} to ${nextRole}?`)) return;
    try {
      const updated = await authedRequest(`/admin/users/${user.id}`, {
        method: "PUT",
        body: JSON.stringify({ role: nextRole })
      });
      if (currentUser?.id === user.id) {
        const next = { ...currentUser, role: updated.role };
        setCurrentUser(next);
      }
      toast("Role updated", "success");
      await refreshManagedUsers();
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const toggleManagedUserStatus = async user => {
    const nextState = !user.is_active;
    if (!window.confirm(`${nextState ? "Activate" : "Deactivate"} ${user.username}'s account?`)) return;
    try {
      await authedRequest(`/admin/users/${user.id}`, {
        method: "PUT",
        body: JSON.stringify({ is_active: nextState })
      });
      toast(`Account ${nextState ? "activated" : "deactivated"}`, "success");
      await refreshManagedUsers();
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const resetManagedUserPassword = async user => {
    const newPassword = window.prompt(`Set new password for ${user.username}:`);
    if (newPassword === null) return;
    if (newPassword.length < 6) {
      toast("Password must be at least 6 characters", "error");
      return;
    }
    try {
      await authedRequest(`/admin/users/${user.id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ newPassword })
      });
      toast("Password reset completed", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const deleteManagedUser = async user => {
    if (!window.confirm(`Delete account "${user.username}" permanently? This cannot be undone.`)) return;
    try {
      await authedRequest(`/admin/users/${user.id}`, { method: "DELETE" });
      toast("User account deleted", "success");
      await refreshManagedUsers();
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const openEditDbModal = dbId => {
    const db = databases.find(item => item.id === (dbId || currentDbId));
    if (!db) return;
    setCurrentDbId(db.id);
    setEditDbForm({
      name: db.name || "",
      description: db.description || "",
      lowBalanceThreshold: db.low_balance_threshold || "",
      approvalThreshold: db.approval_threshold || ""
    });
    setModals(prev => ({ ...prev, editDb: true }));
  };

  const openNewTransactionModal = () => {
    const isFirstTransaction = transactions.filter(txn => !txn.is_voided).length === 0;
    setTxnForm({
      type: isFirstTransaction ? "credit" : "",
      amount: "",
      date: nowInput(),
      sender: "",
      receiver: "",
      mode: "electronic",
      modeData: { elecId: "" },
      location: "",
      notes: "",
      receiptImage: null
    });
    setModals(prev => ({ ...prev, txn: true }));
  };

  const openEditTransactionModal = txn => {
    setEditTxnForm({
      id: txn.id,
      amount: txn.amount,
      date: (txn.date || "").slice(0, 16),
      sender: txn.sender || "",
      receiver: txn.receiver || "",
      location: txn.location || "",
      notes: txn.notes || ""
    });
    setModals(prev => ({ ...prev, editTxn: true }));
  };

  const openVoidModal = txn => {
    setVoidTransactionId(txn.id);
    setVoidReason("");
    setModals(prev => ({ ...prev, voidTxn: true }));
  };

  const openRecurringModal = async () => {
    if (!currentDbId) return;
    await loadRecurring(currentDbId);
    setRecurringForm({ type: "", amount: "", frequency: "monthly", description: "", nextRun: new Date().toISOString().slice(0, 10) });
    setModals(prev => ({ ...prev, recurring: true }));
  };

  const openExportModal = () => {
    const txns = transactions || [];
    let from = "";
    let to = "";
    if (txns.length) {
      const sorted = [...txns].sort((a, b) => new Date(a.date) - new Date(b.date));
      from = sorted[0]?.date?.split("T")[0] || "";
      to = sorted[sorted.length - 1]?.date?.split("T")[0] || "";
    }
    setExportForm({ dateFrom: from, dateTo: to, orgName: "" });
    setModals(prev => ({ ...prev, export: true }));
  };

  const printLedger = () => {
    if (!currentDb) return;
    const txns = transactions.filter(txn => !txn.is_voided);
    const totalCr = txns.filter(txn => txn.type === "credit").reduce((sum, txn) => sum + Number(txn.amount || 0), 0);
    const totalDr = txns.filter(txn => txn.type === "debit").reduce((sum, txn) => sum + Number(txn.amount || 0), 0);
    const printWindow = window.open("", "_blank");
    printWindow.document.write(`
      <style>
        body{font-family:Arial,sans-serif;padding:20px}
        table{width:100%;border-collapse:collapse;margin-top:20px}
        th,td{border:1px solid #ddd;padding:8px;text-align:left;font-size:12px}
        th{background:#333;color:#fff}
        .credit{color:green}.debit{color:red}
      </style>
      <h1>FundVault - ${currentDb.name}</h1>
      <h2>Transaction Ledger</h2>
      <p>Generated on ${formatDate(new Date().toISOString())}</p>
      <div><strong>Total Credits:</strong> ${fmt(totalCr)} • <strong>Total Debits:</strong> ${fmt(totalDr)} • <strong>Balance:</strong> ${fmt(currentDb.balance)}</div>
      <table>
        <tr><th>#</th><th>Date</th><th>Type</th><th>Amount</th><th>Sender</th><th>Receiver</th><th>Mode</th><th>Balance</th></tr>
        ${txns
          .map(
            (txn, idx) => `
            <tr>
              <td>${idx + 1}</td>
              <td>${new Date(txn.date).toLocaleDateString("en-IN")}</td>
              <td class="${txn.type}">${txn.type.toUpperCase()}</td>
              <td class="${txn.type}">${fmt(txn.amount)}</td>
              <td>${txn.sender || "-"}</td>
              <td>${txn.receiver || "-"}</td>
              <td>${txn.mode}</td>
              <td>${fmt(txn.running_balance)}</td>
            </tr>`
          )
          .join("")}
      </table>
    `);
    printWindow.document.close();
    printWindow.print();
  };

  const clearCache = () => {
    if (
      !window.confirm(
        "⚠️ WARNING: Clear Cache\n\nThis will permanently delete all cached data and local preferences.\n\nContinue?"
      )
    ) {
      return;
    }
    localStorage.removeItem("fundvault_token");
    localStorage.removeItem("fundvault_currentUser");
    localStorage.removeItem("fundvault_theme");
    toast("✅ Cache cleared successfully. Refreshing app...", "success");
    setTimeout(() => window.location.reload(), 800);
  };

  useEffect(() => {
    const storedTheme = localStorage.getItem("fundvault_theme");
    if (storedTheme) {
      setTheme(storedTheme);
      document.documentElement.setAttribute("data-theme", storedTheme);
    }
    const savedToken = localStorage.getItem("fundvault_token");
    const savedUser = localStorage.getItem("fundvault_currentUser");
    if (savedToken && savedUser) {
      const user = JSON.parse(savedUser);
      setToken(savedToken);
      setCurrentUser({ ...user, token: savedToken });
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    runPostLoginLoad();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    const onKeyDown = e => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
      const anyModalOpen = Object.values(modals).some(Boolean);
      if (e.key === "Escape" && anyModalOpen) {
        setModals(prev => Object.fromEntries(Object.keys(prev).map(key => [key, false])));
        return;
      }
      if (anyModalOpen) return;
      switch (e.key.toLowerCase()) {
        case "n":
          if (currentDbId) openNewTransactionModal();
          break;
        case "d":
          setModals(prev => ({ ...prev, createDb: true }));
          break;
        case "h":
          setCurrentDbId(null);
          setTransactions([]);
          setActiveTab("home");
          break;
        case "t":
          setTheme(prev => (prev === "light" ? "dark" : "light"));
          break;
        case "?":
          setModals(prev => ({ ...prev, shortcuts: true }));
          break;
        case "1":
          setCurrentDbId(null);
          setTransactions([]);
          setActiveTab("home");
          break;
        case "2":
          setCurrentDbId(null);
          setTransactions([]);
          setActiveTab("dashboard");
          break;
        case "3":
          setCurrentDbId(null);
          setTransactions([]);
          setActiveTab("audit");
          break;
        case "4":
          setCurrentDbId(null);
          setTransactions([]);
          setActiveTab("trash");
          break;
        default:
          break;
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [modals, currentDbId]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("fundvault_theme", theme);
  }, [theme]);

  if (!currentUser || !token) {
    return (
      <>
        <AuthView
          authTab={authTab}
          setAuthTab={setAuthTab}
          loginForm={loginForm}
          setLoginForm={setLoginForm}
          signupForm={signupForm}
          setSignupForm={setSignupForm}
          onLogin={handleLogin}
          onSignup={handleSignup}
        />
        <div className="toast-container">
          {toasts.map(toastItem => (
            <div key={toastItem.id} className={`toast ${toastItem.type}`}>
              {toastItem.message}
            </div>
          ))}
        </div>
      </>
    );
  }

  return (
    <>
      <HeaderBar
        currentUser={currentUser}
        theme={theme}
        onToggleTheme={() => setTheme(prev => (prev === "light" ? "dark" : "light"))}
        onLogout={logout}
        onOpenUserManagement={async () => {
          setModals(prev => ({ ...prev, userManagement: true }));
          await refreshManagedUsers();
        }}
        onOpenProfile={openProfileModal}
        onClearCache={clearCache}
        userDropdownOpen={userDropdownOpen}
        setUserDropdownOpen={setUserDropdownOpen}
      />

      {activeTab !== "db" && <NavTabs activeTab={activeTab} onSwitch={tab => setActiveTab(tab)} />}

      {activeTab === "home" && (
        <HomeView
          databases={databases}
          auditLogs={auditLogs}
          onOpenDb={loadCurrentDb}
          onOpenCreateDb={() => setModals(prev => ({ ...prev, createDb: true }))}
          onOpenMerge={() => setModals(prev => ({ ...prev, merge: true }))}
          onEditDbFromCard={openEditDbModal}
          onDeleteDb={deleteDatabase}
        />
      )}
      {activeTab === "dashboard" && <DashboardView databases={databases} overview={overview} theme={theme} />}
      {activeTab === "audit" && <AuditView auditLogs={auditLogs} filters={auditFilters} setFilters={setAuditFilters} />}
      {activeTab === "trash" && <TrashView items={trashItems} onRestore={restoreTrashItem} onPermanentDelete={permanentDeleteTrashItem} onEmptyTrash={emptyTrash} />}
      {activeTab === "db" && (
        <DatabaseView
          database={currentDb}
          transactions={transactions}
          filters={txnFilters}
          setFilters={setTxnFilters}
          onGoHome={() => {
            setCurrentDbId(null);
            setTransactions([]);
            setActiveTab("home");
          }}
          onOpenEditDb={() => openEditDbModal()}
          onArchiveDb={archiveDatabase}
          onOpenRecurring={openRecurringModal}
          onOpenExport={openExportModal}
          onPrint={printLedger}
          onOpenNewTxn={openNewTransactionModal}
          onOpenEditTxn={openEditTransactionModal}
          onOpenVoidTxn={openVoidModal}
          onApproveTxn={approveTransaction}
          onDeleteVoidedTxn={deleteVoidedTransaction}
          onViewReceipt={txn => {
            setSelectedReceipt(txn.receipt_image);
            setModals(prev => ({ ...prev, receipt: true }));
          }}
        />
      )}

      <AppModals
        modals={modals}
        setModals={setModals}
        state={{
          databases,
          transactions,
          currentDb,
          currentUser,
          recurringItems,
          managedUsers,
          createDbForm,
          editDbForm,
          profileForm,
          txnForm,
          editTxnForm,
          voidReason,
          mergeForm,
          recurringForm,
          exportForm,
          selectedReceipt
        }}
        actions={{
          toast,
          createDatabase,
          saveDatabaseEdit,
          submitTransaction,
          saveTransactionEdit,
          confirmVoidTransaction,
          mergeDatabases,
          addRecurring,
          deleteRecurring,
          refreshManagedUsers,
          editManagedUser,
          toggleManagedUserRole,
          toggleManagedUserStatus,
          resetManagedUserPassword,
          deleteManagedUser,
          setCreateDbForm,
          setEditDbForm,
          setProfileForm,
          setTxnForm,
          setEditTxnForm,
          setVoidReason,
          setMergeForm,
          setRecurringForm,
          setExportForm,
          saveProfile,
          updateProfileImage
        }}
      />

      <div className="toast-container">
        {toasts.map(toastItem => (
          <div key={toastItem.id} className={`toast ${toastItem.type}`}>
            {toastItem.message}
          </div>
        ))}
      </div>
    </>
  );
}
