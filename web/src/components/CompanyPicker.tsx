import { useEffect, useRef, useState } from "react";
import { api, type Company } from "../api";

type Props = {
  placeholder: string;
  onPick: (c: Company) => void;
  exclude?: string[];
  ariaLabel: string;
};

/** 티커·회사명 자동완성 입력. SEC company_tickers 목록에서 검색한다. */
export function CompanyPicker({ placeholder, onPick, exclude = [], ariaLabel }: Props) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Company[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const seq = useRef(0);

  useEffect(() => {
    const query = q.trim();
    if (!query) {
      setItems([]);
      return;
    }
    const id = ++seq.current;
    const t = setTimeout(() => {
      api
        .searchCompanies(query)
        .then((r) => {
          if (id === seq.current) {
            setItems(r.filter((c) => !exclude.includes(c.ticker)));
            setActive(0);
          }
        })
        .catch(() => setItems([]));
    }, 150);
    return () => clearTimeout(t);
  }, [q, exclude.join(",")]);

  const pick = (c: Company) => {
    onPick(c);
    setQ("");
    setItems([]);
    setOpen(false);
  };

  return (
    <div className="picker">
      <input
        value={q}
        placeholder={placeholder}
        aria-label={ariaLabel}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, items.length - 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          else if (e.key === "Enter" && items[active]) {
            e.preventDefault();
            pick(items[active]);
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && items.length > 0 && (
        <ul className="suggest" role="listbox">
          {items.map((c, i) => (
            <li
              key={c.ticker}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(c);
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="tk">{c.ticker}</span>
              <span className="nm">{c.name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
