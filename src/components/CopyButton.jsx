import useCopy from '../hooks/useCopy.js';

export default function CopyButton({ text, label = 'Copy', className = '' }) {
  const [copied, copy] = useCopy(text);
  return (
    <button type="button" className={`copy-btn${copied ? ' is-copied' : ''} ${className}`} onClick={copy} aria-live="polite">
      {copied ? 'Copied' : label}
    </button>
  );
}
