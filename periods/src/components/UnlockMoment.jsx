import { milestoneById } from '../data/rewards.js';
import { paletteById } from '../data/palettes.js';
import Badge from './Badge.jsx';
import Modal from './Modal.jsx';

export default function UnlockMoment({ id, onClose }) {
  const m = milestoneById(id);
  if (!m) return null;
  return (
    <Modal labelledBy="unlock-title" onClose={onClose} className="unlock">
      <div className="unlock__badge">
        <Badge shape={m.badge} size={96} />
      </div>
      <p className="eyebrow">Something new for you</p>
      <h2 id="unlock-title">{m.title}</h2>
      <p>{m.note}</p>
      {m.palette && <p className="muted">New palette unlocked: {paletteById(m.palette).name}. Find it in Settings.</p>}
      {m.insights && <p className="muted">Cycle insights are now open in your Collection.</p>}
      <button type="button" className="button" onClick={onClose} autoFocus>
        Thank you
      </button>
    </Modal>
  );
}
