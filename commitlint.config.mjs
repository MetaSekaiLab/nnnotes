export default {
  extends: ["@commitlint/config-conventional"],
  rules: {
    // bodies and footers wrap freely; the header keeps its length limit
    "body-max-line-length": [0],
    "footer-max-line-length": [0],
  },
};
