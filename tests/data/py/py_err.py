session.run_sql('select "first_result" as myresult');
session.run_sql('select * from unexisting.whatever');
session.run_sql('select "second_result" as secondtry');

