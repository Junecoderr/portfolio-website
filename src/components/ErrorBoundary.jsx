import { Component } from 'react';

/** Keeps one failing subtree (for example a WebGL crash) from taking the page down. */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error, info) {
    if (typeof console !== 'undefined') console.error('[portfolio] section failed', error, info?.componentStack);
  }

  render() {
    if (this.state.failed) return this.props.fallback ?? null;
    return this.props.children;
  }
}
