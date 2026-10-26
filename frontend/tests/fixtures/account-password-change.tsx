import React from "react";
import { createRoot } from "react-dom/client";
import { AccountPasswordChange } from "../../src/account-password-change";
import "../../src/style.css";

createRoot(document.getElementById("root")!).render(<AccountPasswordChange account={{ user_id: "synthetic-member", authentication_required: true, kind: "member", can_change_password: new URLSearchParams(location.search).get("enabled") !== "false" }} />);
