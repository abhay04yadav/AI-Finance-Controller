import { redirect } from "next/navigation";

/**
 * `/` is `/exceptions`. Guide §8.1.
 *
 * Not a landing page, not a summary with exceptions linked from it. The
 * inversion §8.1 asks for is that the worklist IS the index — a controller
 * opens this tool to find out what is broken, and anything placed in front of
 * that is something they have to click past every single morning.
 */
export default function Home() {
  redirect("/exceptions");
}
