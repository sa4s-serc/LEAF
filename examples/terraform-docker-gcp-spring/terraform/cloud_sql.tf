resource "google_sql_database_instance" "cs-aula-spring" {
  database_version = "MYSQL_8_0"
  name             = "cs-aula-spring"
  region           = var.region

  settings {
    tier = "db-n1-standard-8"
    #db-g1-small
  }

  deletion_protection = false
}

resource "google_sql_database" "db-aula-spring" {
  name     = var.db_name
  instance = google_sql_database_instance.cs-aula-spring.name
}

resource "google_sql_user" "db-user-aula-spring" {
  name     = "petclinic"  
  instance = google_sql_database_instance.cs-aula-spring.name
  password = "petclinic"
}
