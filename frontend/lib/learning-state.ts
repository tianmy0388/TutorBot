import { getLearningPath, getProfile } from "./api";
import { useTutorStore } from "./store";

export async function refreshLearningState(userId: string, _course: string) {
  if (!userId) return;
  const [profile, path] = await Promise.all([
    getProfile(userId),
    getLearningPath(userId),
  ]);
  const store = useTutorStore.getState();
  store.setProfile(profile, userId);
  store.setPlannedPath(path, userId);
}
