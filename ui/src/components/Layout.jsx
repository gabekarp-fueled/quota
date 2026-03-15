import React from "react";

const NAV = [
  { href: "#/", label: "Dashboard" },
  { href: "#/agents", label: "Agents" },
  { href: "#/objectives", label: "OKRs" },
  { href: "#/runs", label: "Runs" },
];

export default function Layout({ children, route, onLogout }) {
  const activePath = route === "/" ? "/" : route;

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 bg-white border-r border-gray-200 flex flex-col">
        {/* Logo */}
        <div className="px-5 py-5 border-b border-gray-200">
          <span className="text-lg font-semibold text-gray-900 tracking-tight">Quota</span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-0.5">
          {NAV.map(({ href, label }) => {
            const path = href.replace(/^#/, "");
            const isActive =
              path === "/"
                ? activePath === "/" || activePath === "/dashboard"
                : activePath.startsWith(path);
            return (
              <a
                key={href}
                href={href}
                className={`flex items-center px-3 py-2 text-sm rounded-lg transition-colors ${
                  isActive
                    ? "bg-gray-100 text-gray-900 font-medium"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                }`}
              >
                {label}
              </a>
            );
          })}
        </nav>

        {/* Footer */}
        <div className="px-3 py-4 border-t border-gray-200">
          <button
            onClick={onLogout}
            className="w-full text-left px-3 py-2 text-sm text-gray-500 hover:text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="max-w-6xl mx-auto px-8 py-8">{children}</div>
      </main>
    </div>
  );
}
