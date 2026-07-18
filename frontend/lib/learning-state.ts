import { getProfile, planPath } from "./api";
import { useTutorStore } from "./store";

export async function refreshLearningState(userId: string, course: string) {
  if (!userId || !course) return;
  const profile = await getProfile(userId);
  if (!profile) return;
  useTutorStore.getState().setProfile(profile);
  const path = await planPath(course, profile);
  useTutorStore.getState().setPlannedPath(path);
}
