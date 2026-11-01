import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AppDataProvider } from "./data";
import { ThemeProvider } from "./theme";
import { ToastProvider } from "./toast";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root missing from index.html");

createRoot(root).render(
  <StrictMode>
    <ToastProvider>
      <ThemeProvider>
        <AppDataProvider>
          <App />
        </AppDataProvider>
      </ThemeProvider>
    </ToastProvider>
  </StrictMode>,
);
