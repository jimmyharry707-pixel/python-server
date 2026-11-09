import { useEffect, useMemo } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type TrainingCourse } from "../../api";
import { useMe } from "../../auth";
import { Markdown } from "../../components/Markdown";
import { ErrorBox, Loading } from "../../ui";

export const TRAINING_QUERY_KEY = ["training-course"];

export function TrainingCoursePage() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  // Members can read the course but don't track progress through it.
  const readOnly = useMe().role.toLowerCase() === "member";
  const course = useQuery({ queryKey: TRAINING_QUERY_KEY, queryFn: api.trainingCourse });
  const lessons = course.data?.lessons ?? [];
  const completed = useMemo(() => new Set(course.data?.completed ?? []), [course.data]);
  const index = lessons.findIndex((lesson) => lesson.slug === slug);
  const lesson = index >= 0 ? lessons[index] : undefined;

  const progress = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) =>
      done ? api.completeLesson(id) : api.uncompleteLesson(id),
    onMutate: ({ id, done }) => {
      // Optimistic, so "Next" never waits on the network.
      queryClient.setQueryData<TrainingCourse>(
        TRAINING_QUERY_KEY,
        (prev) =>
          prev && {
            ...prev,
            completed: done
              ? [...new Set([...prev.completed, id])]
              : prev.completed.filter((x) => x !== id),
          },
      );
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: TRAINING_QUERY_KEY }),
  });

  // New lesson: start at the top, or at the #section a cross-link pointed to.
  useEffect(() => {
    if (!lesson) return;
    const id = decodeURIComponent(location.hash.slice(1));
    const target = id ? document.getElementById(id) : null;
    if (target) target.scrollIntoView({ block: "start" });
    else window.scrollTo({ top: 0 });
  }, [lesson?.id, location.hash]);

  if (course.isLoading) return <Loading />;
  if (course.error) return <ErrorBox error={course.error} />;
  if (!lessons.length)
    return (
      <div className="card">
        <div className="empty">
          <div className="empty-icon">
            <i className="ti ti-school" />
          </div>
          <p className="empty-title">The training course is being prepared</p>
          <p className="empty-subtitle text-secondary">
            Check back soon, or ask your manager when it will be available.
          </p>
        </div>
      </div>
    );
  if (!lesson) {
    // /training (or a stale link): resume at the first unfinished lesson.
    const next = (!readOnly && lessons.find((x) => !completed.has(x.id))) || lessons[0];
    return <Navigate to={`/training/${next.slug}`} replace />;
  }

  const total = lessons.length;
  const doneCount = lessons.filter((x) => completed.has(x.id)).length;
  const percent = Math.round((doneCount / total) * 100);
  const isDone = completed.has(lesson.id);
  const prev = lessons[index - 1];
  const next = lessons[index + 1];
  const allDone = !readOnly && doneCount === total;
  const go = (target?: { slug: string }) => target && navigate(`/training/${target.slug}`);
  const showDone = (id: string) => !readOnly && completed.has(id);
  const completeAndContinue = () => {
    if (!isDone) progress.mutate({ id: lesson.id, done: true });
    go(next);
  };

  return (
    <div className="row g-2 g-md-4 stage-course">
      <aside className="col-lg-4 col-xl-3">
        <div className="card stage-course-outline">
          <div className="card-body">
            <div className="d-flex justify-content-between align-items-baseline mb-2">
              <h3 className="card-title mb-0">Bidder training</h3>
              {!readOnly && (
                <span className="text-secondary small">
                  {doneCount}/{total} done
                </span>
              )}
            </div>
            {!readOnly && (
              <div
                className="progress progress-sm mb-3"
                role="progressbar"
                aria-label="Course progress"
                aria-valuenow={percent}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div className="progress-bar bg-success" style={{ width: `${percent}%` }} />
              </div>
            )}
            <select
              className="form-select d-lg-none"
              aria-label="Jump to lesson"
              value={lesson.slug}
              onChange={(event) => navigate(`/training/${event.target.value}`)}
            >
              {lessons.map((x, i) => (
                <option key={x.id} value={x.slug}>
                  {showDone(x.id) ? "✓ " : ""}
                  {i + 1}. {x.title}
                </option>
              ))}
            </select>
            <ol className="stage-course-steps d-none d-lg-block">
              {lessons.map((x, i) => {
                const state = x.id === lesson.id ? "active" : showDone(x.id) ? "done" : "todo";
                return (
                  <li key={x.id}>
                    <Link
                      to={`/training/${x.slug}`}
                      className={`stage-course-step is-${state}`}
                      aria-current={x.id === lesson.id ? "step" : undefined}
                    >
                      <span className="stage-course-step-marker" aria-hidden="true">
                        {showDone(x.id) ? <i className="ti ti-check" /> : i + 1}
                      </span>
                      <span className="stage-course-step-title">{x.title}</span>
                      {showDone(x.id) && <span className="visually-hidden">(completed)</span>}
                    </Link>
                  </li>
                );
              })}
            </ol>
          </div>
        </div>
      </aside>

      <section className="col-lg-8 col-xl-9">
        {allDone && (
          <div className="alert alert-success d-flex align-items-center" role="status">
            <i className="ti ti-trophy me-2 fs-2" aria-hidden="true" />
            <div>
              <strong>Course complete.</strong> Keep the{" "}
              {lessons.some((x) => x.slug === "checklists") ? (
                <Link to="/training/checklists">checklists</Link>
              ) : (
                "checklists"
              )}{" "}
              open while you work, and come back whenever a rule is unclear.
            </div>
          </div>
        )}
        <article className="card stage-course-lesson">
          <div className="card-header d-block">
            <div className="d-flex flex-wrap justify-content-between align-items-start gap-2">
              <div>
                <div className="page-pretitle">
                  Step {index + 1} of {total}
                </div>
                <h2 className="stage-course-lesson-title mb-0">{lesson.title}</h2>
              </div>
              <div className="d-flex align-items-center gap-2">
                {!readOnly && isDone && (
                  <span className="badge bg-success-lt">
                    <i className="ti ti-circle-check me-1" aria-hidden="true" />
                    Completed
                  </span>
                )}
                {course.data?.can_edit && (
                  <Link
                    className="btn btn-sm btn-outline-secondary"
                    to={`/training-editor?lesson=${encodeURIComponent(lesson.id)}`}
                  >
                    <i className="ti ti-edit me-1" aria-hidden="true" />
                    Edit
                  </Link>
                )}
              </div>
            </div>
          </div>
          <div className="card-body">
            <Markdown source={lesson.body_md} />
          </div>
          <div className="card-footer stage-course-nav">
            <button
              type="button"
              className="btn btn-outline-secondary"
              disabled={!prev}
              onClick={() => go(prev)}
            >
              <i className="ti ti-arrow-left me-1" aria-hidden="true" />
              Previous
            </button>
            <div className="d-flex flex-wrap justify-content-end gap-2">
              {readOnly ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={!next}
                  onClick={() => go(next)}
                >
                  Next lesson
                  <i className="ti ti-arrow-right ms-1" aria-hidden="true" />
                </button>
              ) : (
                <>
                  {isDone && (
                    <button
                      type="button"
                      className="btn btn-link text-secondary"
                      onClick={() => progress.mutate({ id: lesson.id, done: false })}
                    >
                      Mark as not done
                    </button>
                  )}
                  {next ? (
                    <button type="button" className="btn btn-primary" onClick={completeAndContinue}>
                      {isDone ? "Next lesson" : "Mark complete & continue"}
                      <i className="ti ti-arrow-right ms-1" aria-hidden="true" />
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn btn-success"
                      disabled={isDone}
                      onClick={() => {
                        progress.mutate({ id: lesson.id, done: true });
                        window.scrollTo({ top: 0, behavior: "smooth" });
                      }}
                    >
                      <i className="ti ti-flag-check me-1" aria-hidden="true" />
                      {isDone ? "Finished" : "Finish course"}
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        </article>
      </section>
    </div>
  );
}
