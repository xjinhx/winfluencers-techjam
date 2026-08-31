// Inlined (not <img src>) so `currentColor` picks up CSS `color` for
// active/inactive tinting -- an <img>-referenced external SVG file can't be
// recolored from the host page. Geometry copied verbatim from the Figma
// export (see src/assets/icons/{home,inbox,profile,more}.svg).

export function HomeIcon(props: { className?: string }) {
  return (
    <svg className={props.className} width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 10L11 3L19 10V18C19 18.2652 18.8946 18.5196 18.7071 18.7071C18.5196 18.8946 18.2652 19 18 19H14V13H8V19H4C3.73478 19 3.48043 18.8946 3.29289 18.7071C3.10536 18.5196 3 18.2652 3 18V10Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

export function InboxIcon(props: { className?: string }) {
  return (
    <svg className={props.className} width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 5H18C18.2652 5 18.5196 5.10536 18.7071 5.29289C18.8946 5.48043 19 5.73478 19 6V16C19 16.2652 18.8946 16.5196 18.7071 16.7071C18.5196 16.8946 18.2652 17 18 17H8L4 20V6C4 5.73478 4.10536 5.48043 4.29289 5.29289C4.48043 5.10536 4.73478 5 5 5H4Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

export function ProfileIcon(props: { className?: string }) {
  return (
    <svg className={props.className} width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M11 11.5C12.933 11.5 14.5 9.933 14.5 8C14.5 6.067 12.933 4.5 11 4.5C9.067 4.5 7.5 6.067 7.5 8C7.5 9.933 9.067 11.5 11 11.5Z" stroke="currentColor" strokeWidth="1.6" />
      <path d="M4 19C4 15 7 13 11 13C15 13 18 15 18 19" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

export function MoreIcon(props: { className?: string }) {
  return (
    <svg className={props.className} width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="5" cy="11" r="1.5" fill="currentColor" />
      <circle cx="11" cy="11" r="1.5" fill="currentColor" />
      <circle cx="17" cy="11" r="1.5" fill="currentColor" />
    </svg>
  );
}
