import { Component, type ErrorInfo, type ReactNode } from "react";
import { RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";

type Props = { children: ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI render error", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex min-h-screen items-center justify-center px-6">
        <div className="w-full max-w-md space-y-3 border bg-background p-6">
          <h1 className="text-lg font-semibold">页面加载出错</h1>
          <p className="text-sm text-muted-foreground">
            {this.state.error.message || "发生了未知错误"}
          </p>
          <p className="text-xs text-muted-foreground">
            可先重新加载页面；如果问题持续出现，请刷新面板服务后重试。
          </p>
          <Button
            onClick={() => {
              window.location.reload();
            }}
          >
            <RotateCw />
            重新加载
          </Button>
        </div>
      </div>
    );
  }
}
