import { CopilotSidebar } from "@copilotkit/react-ui";
import { CopilotKit } from "@copilotkit/react-core";
import { ReactNode } from "react";
import { useAuth } from "../store/auth";

interface ChatProviderProps {
  children: ReactNode;
  activeAccountId?: string;
}

export default function ChatProvider({ children, activeAccountId }: ChatProviderProps) {
  const token = useAuth((s) => s.token);

  return (
    <CopilotKit
      runtimeUrl="/copilotkit"
      headers={{
        "Authorization": `Bearer ${token}`,
        ...(activeAccountId && { "X-Account-Id": activeAccountId }),
      }}
    >
      {children}
      <CopilotSidebar
        labels={{
          title: "Trading Assistant",
        }}
        defaultOpen={false}
      />
    </CopilotKit>
  );
}
