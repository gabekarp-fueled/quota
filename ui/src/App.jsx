import React, { useEffect, useState } from "react";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Objectives from "./pages/Objectives";
import Runs from "./pages/Runs";
import Login from "./pages/Login";

function getRoute() {
  const hash = window.location.hash || "#/";
  return hash.replace(/^#/, "") || "/";
}

export default function App() {
  const [route, setRoute] = useState(getRoute);
  const [token, setToken] = useState(() => localStorage.getItem("quota_token"));

  useEffect(() => {
    const onHashChange = () => setRoute(getRoute());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const handleLogin = (tok) => {
    localStorage.setItem("quota_token", tok);
    setToken(tok);
    window.location.hash = "#/";
  };

  const handleLogout = () => {
    localStorage.removeItem("quota_token");
    setToken(null);
    window.location.hash = "#/login";
  };

  if (!token || route === "/login") {
    return <Login onLogin={handleLogin} />;
  }

  let page;
  if (route === "/" || route === "/dashboard") {
    page = <Dashboard />;
  } else if (route.startsWith("/agents/")) {
    const name = route.replace("/agents/", "");
    page = <AgentDetail name={name} />;
  } else if (route === "/agents") {
    page = <Agents />;
  } else if (route === "/objectives") {
    page = <Objectives />;
  } else if (route === "/runs") {
    page = <Runs />;
  } else {
    page = <Dashboard />;
  }

  return <Layout route={route} onLogout={handleLogout}>{page}</Layout>;
}
