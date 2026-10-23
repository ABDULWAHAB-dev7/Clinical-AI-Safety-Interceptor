import Link from "next/link";
import { PageHeader } from "@/components/PageHeader";

export const metadata = { title: "Page not found · Clinical AI Safety Interceptor" };

export default function NotFound() {
  return (
    <div className="wrap flex flex-col items-center gap-10 pt-12 sm:pt-20">
      <PageHeader eyebrow="404" title={<>Page not <span className="hl">found</span>.</>}>
        <p className="lead max-w-md">That page does not exist. Nothing was checked and nothing was stored.</p>
      </PageHeader>
      <Link href="/" className="btn btn-solid">
        Back to the overview
      </Link>
    </div>
  );
}
