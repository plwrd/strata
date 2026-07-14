/**
 * Catches a failing 3D renderer and falls back to 2D.
 *
 * A lost GPU context, a driver crash or an out-of-memory in Three.js must degrade
 * the *graph*, not the application. Without this, one throw inside the canvas
 * unmounts the whole React tree and the user loses their editor too.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback: ReactNode;
  onError?: (error: Error) => void;
}

interface State {
  failed: boolean;
  message: string;
}

export class GraphErrorBoundary extends Component<Props, State> {
  override state: State = { failed: false, message: "" };

  static getDerivedStateFromError(error: Error): State {
    return { failed: true, message: error.message };
  }

  override componentDidCatch(error: Error, _info: ErrorInfo): void {
    this.props.onError?.(error);
  }

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;

    return (
      <>
        <p className="stage__fallback mono" role="status">
          3D rendering is unavailable on this display (
          {this.state.message.slice(0, 80)}). Showing the 2D graph — everything
          else works normally.
        </p>
        {this.props.fallback}
      </>
    );
  }
}
